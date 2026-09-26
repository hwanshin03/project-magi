"""Immutable analysis snapshots and deterministic historical retrieval."""

import json
import re
from dataclasses import asdict, dataclass
from typing import Optional
from uuid import UUID, uuid4

from magi.decision import AgentResult, Availability, Position
from magi.storage import Database, StorageError, check_sensitive, json_text, timestamp
from magi.voting import FinalAction, VotingEngine, VotingResult


@dataclass(frozen=True)
class AnalysisRecord:
    run_id: str
    timestamp: str
    question: str
    vote: VotingResult
    explanation: Optional[str]


def _vote_data(vote):
    data = asdict(vote)
    del data['agent_results']
    return data


class AnalysisMemory:
    def __init__(self, database=None):
        self.database = database if database is not None else Database()

    def save_analysis(self, question, vote, explanation=None, *, executed_at=None):
        if not isinstance(question, str) or not question.strip():
            raise ValueError('A nonempty analysis question is required')
        if explanation is not None and not isinstance(explanation, str):
            raise ValueError('Explanation must be text or None')
        expected = VotingEngine().vote({r.agent: r for r in vote.agent_results})
        if vote != expected:
            raise ValueError('Voting snapshot does not match agent decisions')
        record = AnalysisRecord(str(uuid4()), timestamp(executed_at), question, vote, explanation)
        check_sensitive(asdict(record))
        with self.database.connect() as connection:
            connection.execute(
                'INSERT INTO analysis_runs VALUES (?, ?, ?, ?, ?, ?, ?)',
                (record.run_id, record.timestamp, question, vote.final_action.value,
                 '2of3-v1', json_text(_vote_data(vote)), explanation))
            for result in vote.agent_results:
                connection.execute(
                    'INSERT INTO analysis_agents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (record.run_id, result.agent, result.provider, result.model,
                     result.position.value if result.position else None, result.confidence,
                     result.reasoning, json_text(result.key_risks), json_text(result.evidence_gaps),
                     result.changed_position, result.availability.value, result.error,
                     result.attempts, result.http_status))
        return record

    def _decode(self, connection, row):
        try:
            UUID(row['run_id'])
            if timestamp(row['timestamp']) != row['timestamp'] or row['voting_version'] != '2of3-v1':
                raise ValueError('Invalid metadata')
            if not isinstance(row['question'], str) or not row['question'].strip():
                raise ValueError('Invalid question')
            if row['explanation'] is not None and not isinstance(row['explanation'], str):
                raise ValueError('Invalid explanation')
            agents = []
            for item in connection.execute('SELECT * FROM analysis_agents WHERE run_id = ?', (row['run_id'],)):
                if item['changed_position'] not in (None, 0, 1):
                    raise ValueError('Invalid change state')
                if (type(item['attempts']) is not int or item['attempts'] < 0
                        or (item['error'] is not None and not isinstance(item['error'], str))
                        or (item['http_status'] is not None and type(item['http_status']) is not int)):
                    raise ValueError('Invalid diagnostics')
                agents.append(AgentResult(
                    agent=item['agent'], provider=item['provider'], model=item['model'],
                    position=Position(item['position']) if item['position'] is not None else None,
                    confidence=item['confidence'], reasoning=item['reasoning'],
                    key_risks=json.loads(item['key_risks_json']), evidence_gaps=json.loads(item['evidence_gaps_json']),
                    changed_position=None if item['changed_position'] is None else bool(item['changed_position']),
                    availability=Availability(item['availability']), error=item['error'],
                    attempts=item['attempts'], http_status=item['http_status']))
            vote = VotingEngine().vote({r.agent: r for r in agents})
            if json_text(_vote_data(vote)) != json_text(json.loads(row['voting_json'])):
                raise ValueError('Invalid voting snapshot')
            if vote.final_action.value != row['final_action']:
                raise ValueError('Inconsistent final action')
            record = AnalysisRecord(row['run_id'], row['timestamp'], row['question'], vote, row['explanation'])
            check_sensitive(asdict(record))
            return record
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            raise StorageError('Invalid stored analysis record.') from None

    def get_analysis(self, run_id):
        with self.database.connect() as connection:
            row = connection.execute('SELECT * FROM analysis_runs WHERE run_id = ?', (run_id,)).fetchone()
            return self._decode(connection, row) if row else None

    def search(self, *, keyword=None, symbol=None, final_action=None, agent=None, position=None, limit=20):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('Limit must be between 1 and 1000')
        clauses, values = [], []
        for term in (keyword, symbol):
            if term:
                # Literal, case-insensitive substring search: no wildcard injection.
                clauses.append('instr(lower(r.question), lower(?)) > 0')
                values.append(term)
        if final_action is not None:
            clauses.append('r.final_action = ?')
            values.append(FinalAction(final_action).value)
        if agent is not None or position is not None:
            conditions = ['a.run_id = r.run_id']
            if agent is not None:
                conditions.append('a.agent = ?')
                values.append(agent)
            if position is not None:
                conditions.append('a.position = ?')
                values.append(Position(position).value)
            clauses.append('EXISTS (SELECT 1 FROM analysis_agents a WHERE ' + ' AND '.join(conditions) + ')')
        query = 'SELECT r.* FROM analysis_runs r'
        if clauses:
            query += ' WHERE ' + ' AND '.join(clauses)
        query += ' ORDER BY r.timestamp DESC, r.run_id DESC LIMIT ?'
        with self.database.connect() as connection:
            return tuple(self._decode(connection, row) for row in connection.execute(query, (*values, limit)).fetchall())

    def recent(self, limit=20):
        return self.search(limit=limit)

    def relevant(self, question):
        stopwords = {'should', 'could', 'would', 'about', 'what', 'when', 'which', 'where',
                     'this', 'that', 'with', 'from', 'have', 'more', 'less', 'than', 'does',
                     'buy', 'sell', 'hold', 'stock', 'shares', 'please', 'analyze', 'analysis',
                     'the', 'and', 'for', 'are', 'you', 'how', 'now', 'can'}
        terms = tuple(dict.fromkeys(word.lower() for word in re.findall(r'\b[\w.-]{2,}\b', question)
                                   if word.lower() not in stopwords))[:20]
        if not terms:
            return ()
        # Bind all user-derived terms. Exact token matches avoid NVDA matching NVDAX.
        clauses = ' OR '.join('instr(lower(question), ?) > 0' for _ in terms)
        matches = []
        with self.database.connect() as connection:
            for row in connection.execute('SELECT * FROM analysis_runs WHERE ' + clauses +
                                          ' ORDER BY timestamp DESC, run_id DESC', terms):
                if not isinstance(row['question'], str) or not row['question'].strip():
                    raise StorageError('Invalid stored analysis record.')
                tokens = set(re.findall(r'\b[\w.-]{2,}\b', row['question'].lower()))
                if tokens.intersection(terms):
                    matches.append(self._decode(connection, row))
                    if len(matches) == 3:
                        break
        return tuple(matches)
