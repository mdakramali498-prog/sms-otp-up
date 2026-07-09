# 🤖 smsotps.com Telegram Bot

smsotps.com API দিয়ে অটোমেটিক ফোন নম্বর কেনা এবং OTP রিসিভ করার টেলিগ্রাম বট।

---

## ⚙️ সেটআপ

### ১. লাইব্রেরি ইনস্টল
```bash
pip install -r requirements.txt
```

### ২. config.json আপডেট করুন
```json
{
  "bot_token": "আপনার_বট_টোকেন",
  "admin_id": 123456789
}
```

| ভেরিয়েবল | কীভাবে পাবেন |
|-----------|-------------|
| `bot_token` | [@BotFather](https://t.me/BotFather) থেকে |
| `admin_id` | [@userinfobot](https://t.me/userinfobot) থেকে |

### ৩. বট চালান
```bash
python main.py
```

---

## 🚀 বট ব্যবহার

1. টেলিগ্রামে বটে `/start` লিখুন
2. [smsotps.com/profile](https://smsotps.com/profile) থেকে API Key নিন
3. API Key পাঠান → লগইন হয়ে যাবে
4. **"📱 নম্বর কিনুন"** চাপুন → Provider → Service → দেশ সিলেক্ট করুন
5. নম্বর পাওয়ার পর ব্যবহার করুন — OTP আসলে অটো নোটিফিকেশন পাবেন

---

## ✨ ফিচার

- 🔐 API Key দিয়ে লগইন (প্রতিটি ইউজার আলাদা)
- 💰 ব্যালেন্স চেক
- 📱 নম্বর কেনা — Provider A, B, D সাপোর্ট
- 🌍 ২০০+ দেশ সাপোর্ট
- ⏳ অটো OTP পোলিং (প্রতি ২০ সেকেন্ডে, ১৫ মিনিট পর্যন্ত)
- ❌ নম্বর ক্যান্সেল
- 🔄 OTP Resend
- 📋 অর্ডার হিস্ট্রি
- 👑 Admin Panel
