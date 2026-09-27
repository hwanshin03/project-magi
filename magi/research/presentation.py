"""Optional labels only; translations never enter persisted domain values."""
LABELS = {
    'en': {'PRIMARY':'Primary source','AUTHORITATIVE_DATA':'Authoritative data',
           'SECONDARY':'Secondary source','TERTIARY':'Tertiary source','INTERNAL_HISTORY':'Internal history',
           'EVIDENCE':'Evidence','CONFLICTING_EVIDENCE':'Conflicting evidence'},
    'ko': {'PRIMARY':'1차 자료','AUTHORITATIVE_DATA':'공신력 있는 데이터',
           'SECONDARY':'2차 자료','TERTIARY':'3차 자료','INTERNAL_HISTORY':'내부 기록',
           'EVIDENCE':'근거 자료','CONFLICTING_EVIDENCE':'상충하는 근거'},
}


def label(value,language='en'):
    return LABELS[language][getattr(value,'value',value)]
