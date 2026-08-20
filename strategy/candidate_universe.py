"""
Broad candidate ticker pool for the earnings-bet strategy. No price-band
bias needed here (fractional shares handle any price) -- just liquid,
well-known US names across sectors, for cross-sectional sample size.
"""
CANDIDATE_POOL = [
    # Mega-cap tech / semis
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AVGO", "ORCL",
    "CRM", "ADBE", "AMD", "INTC", "CSCO", "QCOM", "TXN", "MU", "NOW", "PANW",
    "SNOW", "PLTR", "UBER", "SHOP", "PYPL", "DELL", "HPQ", "IBM", "WDC",
    "STX", "ON", "SWKS", "MCHP", "NXPI", "ADI", "LRCX", "KLAC", "AMAT",
    "ANET", "FTNT", "CDNS", "SNPS", "MRVL", "TEAM", "WDAY", "DDOG", "NET",
    "ZS", "CRWD", "OKTA", "TWLO", "MDB", "HUBS", "ZM", "DOCU", "PATH",

    # Financials
    "JPM", "BAC", "WFC", "C", "GS", "MS", "SCHW", "AXP", "USB", "PNC",
    "TFC", "COF", "STT", "FITB", "KEY", "RF", "HBAN", "CFG", "MTB", "ALLY",
    "BK", "BLK", "SPGI", "ICE", "CME", "MCO", "AON", "MMC", "TRV", "PGR",
    "ALL", "MET", "PRU", "AIG", "V", "MA", "DFS", "SYF", "FIS", "FISV",

    # Healthcare
    "UNH", "PFE", "MRK", "ABBV", "BMY", "GILD", "AMGN", "LLY", "JNJ", "TMO",
    "ABT", "DHR", "MDT", "SYK", "ISRG", "BSX", "CVS", "CI", "HUM", "ELV",
    "MRNA", "REGN", "VRTX", "BIIB", "ZTS", "DVA", "MOH", "CNC", "HCA",

    # Consumer discretionary
    "HD", "LOW", "NKE", "SBUX", "MCD", "TGT", "TJX", "ROST", "GM", "F",
    "MAR", "HLT", "YUM", "DHI", "LEN", "BBY", "DG", "DLTR", "EBAY", "ETSY",
    "BKNG", "ABNB", "RCL", "CCL", "NCLH", "LVS", "WYNN", "MGM", "DRI", "CMG",

    # Consumer staples
    "KO", "PEP", "PG", "WMT", "COST", "MDLZ", "CL", "KMB", "GIS", "K",
    "HSY", "STZ", "KR", "SYY", "TAP", "MNST", "KDP", "CLX", "CHD", "MKC",

    # Industrials
    "GE", "HON", "CAT", "DE", "UPS", "FDX", "BA", "LMT", "RTX", "UNP",
    "CSX", "NSC", "EMR", "ETN", "ITW", "PH", "CMI", "DAL", "UAL", "LUV",
    "GD", "NOC", "TDG", "PCAR", "ROK", "IR", "XYL", "DOV", "SWK", "MAS",

    # Energy
    "XOM", "CVX", "COP", "OXY", "SLB", "HAL", "MRO", "DVN", "FANG", "KMI",
    "WMB", "PSX", "VLO", "MPC", "BKR", "HES", "EOG", "APA",

    # Materials
    "LIN", "APD", "FCX", "NUE", "DOW", "DD", "NEM", "ALB", "CE", "ECL",
    "PPG", "SHW", "VMC", "MLM",

    # Utilities
    "NEE", "DUK", "SO", "D", "AEP", "EXC", "XEL", "ED", "PEG", "FE",
    "WEC", "ES", "AEE", "CMS", "DTE",

    # Communication services
    "DIS", "CMCSA", "T", "VZ", "TMUS", "CHTR", "WBD", "PARA", "SNAP", "PINS",
    "NFLX", "SPOT", "MTCH", "LYV",

    # Real estate
    "SPG", "O", "PLD", "AMT", "EQIX", "PSA", "AVB", "EQR", "DLR", "WELL",

    # Additional mid/large caps for cross-sectional depth
    "ADP", "PAYX", "CTAS", "FAST", "ODFL", "VRSK", "GWW", "TT", "JCI",
    "CARR", "OTIS", "IEX", "AME", "ROP", "TRMB", "PWR", "URI", "WAB",
]
