# ArtPM

Professional project management tool for game art production.

## Start

```bash
start.bat
```

## Features

- Project tracking
- Document parsing
- Analytics dashboard
- Token monitoring

## Setup

Edit `.env` for API keys:

```env
OPENAI_API_KEY=sk-your-key
ANTHROPIC_API_KEY=sk-ant-your-key
```

Get keys: https://platform.openai.com/api-keys

## Requirements

Python 3.8+

## Manual

```bash
pip install streamlit pandas plotly sqlalchemy openpyxl
streamlit run artpm_agent/app.py
```
