# 📍 Location Access Attendance System (LAAS)
🚀 **A smart attendance system based on real-time location tracking.**

---

### 🌟 Overview
This project is a **Geofencing-based Attendance System** built using Python (Flask). It ensures students can only mark their attendance if they are physically present within a specific range (e.g., 50 meters) of the teacher/classroom.

---

### ✨ Key Features
* 📍 **Geofencing:** Marks attendance only within the allowed radius.
* 👨‍🏫 **Teacher Dashboard:** Complete control over starting and stopping sessions.
* 🎓 **Student Portal:** Easy registration and real-time attendance marking.
* 🔒 **Anti-Proxy:** Prevents students from marking attendance remotely.
* ⚡ **Fast Performance:** Optimized SQLite backend for quick verification.

---

### 🛠️ Tech Stack
| Component | Technology |
| :--- | :--- |
| **Backend** | Python (Flask) |
| **Database** | SQLite / SQLAlchemy |
| **Frontend** | HTML5, CSS3, JavaScript |
| **Logic** | Geolocation API & Haversine Formula |

---

### 📂 Project Structure
```bash
LAMS-PROJECT/
├── app.py           # Main backend & API logic
├── models.py        # Database schema (User, Attendance)
├── static/          # CSS, JS, and Images
└── templates/       # HTML layouts for Login, Dashboard, etc.

Installation & Setup
1.Clone the repository- https://github.com/shivansh1918/LAMS-PROJECT.git
2.Navigate to project folder
cd LAMS-PROJECT
3.Install dependencies-pip install -r requirements.txt
4.Run the application- python app.py
5.Open in browser
Go to: http://127.0.0.1:5000

-Usage
Students: Register and wait for admin approval.
Admin: Approve or reject student requests.
Teacher: Start an attendance session.
Marking: Students mark attendance within the allowed geofence range

🌟 Support
If you like this project, please ⭐ star this repository on GitHub. It really helps and motivates further development!
Author: Shivansh Vashishth
