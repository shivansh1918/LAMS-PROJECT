# 📍 Location Access Attendance System (LAAS)

🚀 A smart attendance system based on real-time location tracking.

## 📌 Features
- 📍 Location-based attendance (within range only)
- 👨‍🏫 Teacher session control
- 🎓 Student attendance marking
- 🔒 Prevents proxy attendance
- ⚡ Fast and secure system

## 🛠️ Tech Stack
- Python (Flask)
- HTML, CSS, JavaScript
- SQLite / Database
- Geolocation API

## ▶️ How It Works
1. Teacher starts session with current location
2. Students can mark attendance only within range (e.g., 50m)
3. System verifies location and marks attendance

## 📂 Project Structure
- `app.py` → Main backend
- `models.py` → Database models
- `templates/` → HTML files
- `static/` → CSS & JS

## ⚙️ Installation

```bash
git clone https://github.com/your-username/your-repo-name.git
cd your-repo-name
pip install -r requirements.txt
python app.py
