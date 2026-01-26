# Monarch Money Weekly Report

Automated 4-week financial summary email sent every Sunday morning.

## Features
- 📈 Net worth snapshot (investments & debt tracking with period-over-period changes)
- 💵 Cashflow summary (income, expenses, savings rate)
- 📊 Spending by category breakdown

## Setup

### 1. Fork/clone this repo

### 2. Add GitHub Secrets
Go to **Settings → Secrets and variables → Actions → New repository secret**

Add these secrets:
| Secret | Value |
|--------|-------|
| `MONARCH_EMAIL` | Your Monarch Money email |
| `MONARCH_PASSWORD` | Your Monarch Money password |
| `MONARCH_MFA_SECRET` | Your MFA secret key (from Settings → Security) |
| `SMTP_SERVER` | `smtp.mail.me.com` (for iCloud) |
| `SMTP_PORT` | `587` |
| `SMTP_EMAIL` | Your iCloud email |
| `SMTP_PASSWORD` | Your iCloud app-specific password |
| `RECIPIENT_EMAILS` | Comma-separated emails (e.g., `you@email.com,spouse@email.com`) |

### 3. Enable Actions
Go to the **Actions** tab and enable workflows if prompted.

### 4. Test it
Click **Actions → Weekly Monarch Money Report → Run workflow** to test manually.

## Schedule
Runs automatically every Sunday at 8:00 AM CST (14:00 UTC).
