# 🏎️ ApexF1 Bot

A Formula 1 information bot for the **Bale messaging platform**, built with Python.

ApexF1 provides users with up-to-date Formula 1 information, including race schedules, session times, results, driver standings, and team rankings.

## ✨ Features

- 🏁 View the next and previous Grand Prix
- 📅 Browse the 2026 Formula 1 race calendar
- ⏰ View session schedules in Tehran time
- 🏆 Check race and qualifying results
- 🏎️ View driver and constructor standings
- 👤 Explore driver profiles
- 🔍 Search for drivers and teams
- ⭐ Follow favorite drivers and teams
- 🔔 Receive race result notifications
- ⚙️ Customize notification settings

## 🛠️ Technologies

- Python
- SQLite
- REST API
- Jolpica F1 API
- Bale Bot API

## 🚀 Installation

1. Clone the repository:

```bash
git clone https://github.com/YOUR_USERNAME/apex-f1-bot.git
cd apex-f1-bot
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Create a `.env` file in the project directory:

```env
BALE_BOT_TOKEN=your_bot_token
```

4. Run the bot:

```bash
python main.py
```

## 🔐 Environment Variables

| Variable | Description |
|---|---|
| `BALE_BOT_TOKEN` | Your Bale bot token |

**Important:** Never share your bot token or upload your `.env` file to GitHub.

## 📡 Data Source

Formula 1 data is retrieved from the [Jolpica F1 API](https://api.jolpi.ca/ergast/f1/).

## 📌 Project Status

Developed as a Python project for providing Formula 1 information through the Bale messaging platform.

## 📄 License

This project is available for educational and personal use.
