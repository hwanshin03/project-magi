"""Explicit economic mappings. Order is a tie-break AFTER period and filing recency."""
SEC = {
 'revenue':('RevenueFromContractWithCustomerExcludingAssessedTax','Revenues','SalesRevenueNet'),
 'operating_income':('OperatingIncomeLoss',), 'net_income':('NetIncomeLoss','ProfitLoss'),
 'eps_basic':('EarningsPerShareBasic',), 'eps_diluted':('EarningsPerShareDiluted',),
 'assets':('Assets',), 'liabilities':('Liabilities',),
 'equity':('StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest','StockholdersEquity'),
 'cash':('CashAndCashEquivalentsAtCarryingValue',), 'debt':('DebtCurrentAndNoncurrent',),
 'operating_cash_flow':('NetCashProvidedByUsedInOperatingActivities',),
 'investing_cash_flow':('NetCashProvidedByUsedInInvestingActivities',),
 'financing_cash_flow':('NetCashProvidedByUsedInFinancingActivities',),
 'capex':('PaymentsToAcquirePropertyPlantAndEquipment',),
}
DART = {
 'revenue':('ifrs-full_Revenue','ifrs_Revenue'),
 'operating_income':('dart_OperatingIncomeLoss',),
 'net_income':('ifrs-full_ProfitLoss','ifrs_ProfitLoss'),
 'eps_basic':('ifrs-full_BasicEarningsLossPerShare',),
 'eps_diluted':('ifrs-full_DilutedEarningsLossPerShare',),
 'assets':('ifrs-full_Assets','ifrs_Assets'), 'liabilities':('ifrs-full_Liabilities','ifrs_Liabilities'),
 'equity':('ifrs-full_Equity','ifrs_Equity'), 'cash':('ifrs-full_CashAndCashEquivalents',),
 'debt':(),  # No aggregation of arbitrary liability accounts.
 'operating_cash_flow':('ifrs-full_CashFlowsFromUsedInOperatingActivities',),
 'investing_cash_flow':('ifrs-full_CashFlowsFromUsedInInvestingActivities',),
 'financing_cash_flow':('ifrs-full_CashFlowsFromUsedInFinancingActivities',),
 'capex':('ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities',),
}
INCOME=('revenue','operating_income','net_income')
BALANCE=('assets','liabilities','equity','cash','debt')
CASH_FLOW=('operating_cash_flow','investing_cash_flow','financing_cash_flow','capex')
PER_SHARE=('eps_basic','eps_diluted')
SEC_CONCEPTS=tuple(sorted({c for values in SEC.values() for c in values}))
