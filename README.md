# Stock Sentiment Analyzer

Stock screener and predictor powered by FinBERT NLP and technical analysis. Finds stocks with strong 3-month performance, fetches recent news, and predicts movement using sentiment analysis.

**DISCLAIMER: This tool is for educational purposes only. It does NOT constitute financial advice.**

## Installation

```bash
pip install stock-sentiment-analyzer
```

## Usage

```bash
# Run screener once
stock-sentiment

# Auto-run daily with alerts
stock-sentiment --schedule

# Auto-run every 12 hours
stock-sentiment --schedule --every 12

# Check past prediction accuracy
stock-sentiment --backtest

# Show recent alerts
stock-sentiment --alerts
```

### Options

| Flag | Description | Default |
|------|-------------|---------|
| `--min-return` | Minimum 3-month return % | 10% |
| `--top` | Number of top stocks to show | 30 |
| `--schedule` | Auto-run on a schedule | off |
| `--every` | Schedule interval in hours | 24 |
| `--backtest` | Check past prediction accuracy | off |
| `--alerts` | Show recent alerts | off |
| `--cloud` | Save HTML report to S3 + email via SES | off |

## License

MIT
