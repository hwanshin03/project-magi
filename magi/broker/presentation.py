"""Privacy-preserving labels; no raw account identifier or payload rendering."""
from .models import BrokerErrorCode

STATUS_LABELS = {
    'en': {'MATCH':'Matched', 'QUANTITY_MISMATCH':'Quantity mismatch', 'COST_MISMATCH':'Average cost mismatch',
           'BROKER_ONLY':'Broker only', 'MAGI_ONLY':'MAGI only', 'AMBIGUOUS':'Ambiguous', 'UNAVAILABLE':'Unavailable'},
    'ko': {'MATCH':'일치', 'QUANTITY_MISMATCH':'수량 불일치', 'COST_MISMATCH':'평균 매입가 불일치',
           'BROKER_ONLY':'증권사 계좌에만 존재', 'MAGI_ONLY':'MAGI에만 존재', 'AMBIGUOUS':'비교 대상 불명확', 'UNAVAILABLE':'조회 불가'},
}
ERRORS = {
    BrokerErrorCode.UNAVAILABLE: 'Broker account data is temporarily unavailable.',
    BrokerErrorCode.AUTHENTICATION: 'Broker authentication failed. Check credentials and allowed IP settings.',
    BrokerErrorCode.RATE_LIMITED: 'Broker account data is rate limited. Try again later.',
    BrokerErrorCode.INVALID_RESPONSE: 'Broker returned an invalid account-data response.',
    BrokerErrorCode.UNSUPPORTED: 'This broker capability is not supported by the read-only adapter.',
    BrokerErrorCode.NOT_FOUND: 'Broker account data could not be found.',
    BrokerErrorCode.AMBIGUOUS: 'Select one account using --account and its displayed index.',
}


def account_label(index):
    return f'Account {index} [identifier masked]'


def value_or_na(value):
    return 'N/A' if value is None else str(value)
