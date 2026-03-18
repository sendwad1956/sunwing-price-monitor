# Sunwing Price Monitor

This is a local Python tool that watches Sunwing package pages, saves price history, and builds an HTML dashboard.

## What it does
- Checks configured Mexico destination pages
- Pulls visible package prices it can find
- Saves every run into a CSV history file
- Builds `sunwing_dashboard.html`
- Shows the cheapest current deal per destination
- Lets you edit destinations, travel window, and alert threshold

## What it does **not** do
- It does not guarantee the final booking price
- It does not use a private Sunwing API
- It may need a quick parser update if Sunwing changes its page structure

## Setup
1. Install Python 3.11+ if needed
2. Open a terminal in this folder
3. Install dependencies:

```bash
pip install requests beautifulsoup4
```

## First run
```bash
python sunwing_price_monitor.py --config config.json --output output
```

After it runs, open:
- `output/sunwing_dashboard.html`

## Config
Edit `config.json` to change:
- origin city
- travelers
- start/end date
- destinations
- alert threshold

## Suggested Windows Scheduled Task
Run once every morning:

Program/script:
```text
C:\Python313\python.exe
```

Add arguments:
```text
C:\Path\To\sunwing_price_monitor.py --config C:\Path\To\config.json --output C:\Path\To\output
```

Start in:
```text
C:\Path\To\folder
```

## Good next upgrade
- Email alerts
- Text message alerts
- A better parser if you lock onto one exact Sunwing results page
- Multiple departure cities
