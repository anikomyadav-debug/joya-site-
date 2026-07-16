# JOYA Website — Real Server with Login

Yeh ek **real website server** hai (jaise sabhi websites hote hain) — login, database,
sessions, admin panel sab included. Koi extra software install nahi karna — sirf Python.

## ▶ Kaise chalayein (2 tarike)

**Sabse aasan:** `START_WEBSITE.bat` par **double-click** karo.

**Ya terminal se:**
```
cd website
python server.py
```

Phir browser me kholo:  **http://localhost:8000/**

> Server chalta rahe isliye `.bat` window / terminal **khula rakho**.
> Band karne ke liye Ctrl+C dabao ya window band kar do.

## 🔑 Login system kaise kaam karta hai

1. Website kholte hi **login page** aata hai — bina login andar nahi ja sakte.
2. **Create account** tab se signup karo.
3. **Jo sabse pehle signup karega wo ADMIN ban jayega** (aap pehle account banao).
4. Login ke baad poori website khulti hai.

## 👑 Admin panel (saara data yahan dikhega)

Admin login karke jaao:  **http://localhost:8000/admin.html**

Wahan dikhega:
- Har user ka **naam, email, phone**
- **Signup date**, **last login**, **kitni baar login kiya**
- **Free / Pro** status (aur Pro banane/hatane ka button)
- Total users, Pro members, active sessions ke **stats**
- **Payment Orders** table — jab koi user pay karke reference submit kare, wahan **Approve** dabate hi wo user auto **Pro** ban jayega
- **Export CSV** button — saara data Excel me download

Nav bar me bhi login user ka naam + **Log out** button aata hai, aur admin ke liye **Admin** link.

## 🔗 Sab kaise connected hai (poora flow)

1. User **signup/login** karta hai → website khulti hai.
2. **Download** button (`/download`) sirf logged-in user ko installer deta hai (server se, secure).
3. Buy section me **Unlock Pro** → UPI QR / Razorpay dikhta hai.
4. Pay karne ke baad user **"Confirm your payment"** form me reference daalta hai → ek **order** ban jata hai.
5. Website user ko **"⏳ verification pending"** dikhati hai.
6. **Admin** dashboard me wo order dikhta hai → **Approve** dabao.
7. User turant **★ Pro** ban jata hai — website pe uska Pro banner + Download button aa jata hai.

Sab kuch ek hi database (`users.db`) aur ek hi server se connected hai — koi manual step nahi.


## 🗄️ Data kahan store hota hai

- Sab kuch ek file me:  **`website/users.db`** (SQLite database)
- Passwords kabhi plain text me nahi — **PBKDF2 se hashed** hote hain (secure)
- Yeh file aapke apne PC/server par rehti hai — 100% aapke paas

## 🌍 Internet par live karna ho (optional)

Abhi yeh `localhost` (sirf aapke PC) par chalta hai. Duniya ko dikhane ke liye:
- Kisi bhi Python-supported host par `server.py` deploy karo, ya
- Apne PC ko temporarily expose karne ke liye tools jaise `ngrok` use karo:
  `ngrok http 8000`

Port badalna ho to:  `set PORT=5000 && python server.py`

## 📁 Files
- `server.py` — the server (login, database, admin API)
- `login.html` — login / signup page
- `admin.html` — admin dashboard (all user data)
- `index.html` — main website (login ke peeche protected)
- `START_WEBSITE.bat` — one-click start
- `users.db` — database (server pehli baar chalne par ban jati hai)
