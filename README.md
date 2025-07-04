# Python SMM Panel Telegram Bot

This is a fully automated, professional SMM Panel bot for Telegram, written in Python using the `python-telegram-bot` library. It's designed for 24/7 deployment on platforms like Koyeb.

## Features

- **User-Friendly Interface**: Clean, inline keyboard-based UI.
- **Automated Order Process**: Users can browse categories, select services, and place orders automatically.
- **Funds Management**: Users can add funds via UPI, with an admin approval system.
- **Referral System**: Users can earn coins by referring others.
- **Admin Panel**: A powerful backend for managing users, orders, deposits, and bot settings.
- **SMM Panel Integration**: Seamlessly connects to any SMM panel that supports the standard API.
- **24/7 Uptime**: Built to be deployed on cloud platforms like Koyeb.

## Deployment on Koyeb

1.  **Fork this repository** to your own GitHub account.
2.  **Create a new App** on your [Koyeb Dashboard](https://app.koyeb.com/).
3.  **Choose GitHub** as the deployment method and select this repository.
4.  In the **Environment Variables** section, add the following secrets:

| Variable Name      | Description                                          | Example Value                                  |
| ------------------ | ---------------------------------------------------- | ---------------------------------------------- |
| `BOT_TOKEN`        | Your Telegram bot token from @BotFather.             | `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`     |
| `ADMIN_ID`         | Your personal Telegram User ID.                      | `5868863582`                                   |
| `SMM_API_KEY`      | Your API key from the SMM panel.                     | `b83a3ad24b77530ec6804bc901a5ed4c`             |
| `SMM_API_URL`      | The API endpoint URL of your SMM panel.              | `https://n1panel.com/api/v2`                   |
| `CHANNEL_1`        | Username of the first channel to join.               | `@Social_Kart`                                 |
| `CHANNEL_2`        | Username of the second channel to join.              | `@Social_Kart`                                 |
| `PAYMENT_CHANNEL`  | Channel where approved payments are logged.          | `@Social_Kart`                                 |
| `UPI_ID`           | The UPI ID for payments.                             | `yourupi@bank`                                 |
| `MARKUP_PERCENT`   | Percentage markup on SMM panel prices.               | `20`                                           |
| `REFERRAL_PERCENT` | Bonus percentage for the referrer on first deposit.  | `10`                                           |
| `BONUS_ENABLED`    | Enable or disable the daily bonus feature.           | `True`                                         |
| `REDEEM_ENABLED`   | Enable or disable the redeem code feature.           | `True`                                         |


5.  Ensure the **Run command** is set to `python bot.py`.
6.  Click **Deploy**. Koyeb will build and run your bot.

---
