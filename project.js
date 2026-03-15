
        // const subjects = { 1:["Computer fundamental & programming", "Fundamental of Management", "Language and communication", "Mathematics-1", "Personal Computer Software"], 2: ["Digital Electronics","Discrete Mathematics","Mathematics-2","Programming in c","Managerial Economics"], 3: ["**[Semester 3]-","Computer oriented numerical analysis", "Computer organization", "Data structure using C", "Object Oriented Programming using C++", "Organization behaviour"], 4: ["**[Semester 4]-","Operating System", "Introduction to DBMS and SQL", "Management Information System", "Visual Basic", "System Analysis and Design"], 5: ["**[Semester 5]-","Computer Graphics And Animation", "Computer network", "Introduction to internet Programming", "Software Engineering", "Advance Computer Architecture"], 6: ["**[Semester 6]-","Multimedia concept and Application", "Artificial Intelligence", "web Technology", "Introduction to .Net"]};
        // let currentAuthRole = "";

        // // UI for Registration Semester
        // document.getElementById('r-role').addEventListener('change', function () {
        //     document.getElementById('r-sem').classList.toggle('hidden', this.value !== 'student');
        // });

        // function showPage(id) {
        //     document.querySelectorAll('.container > div, #landing-page, #features-page').forEach(p => p.classList.add('hidden'));
        //     document.getElementById(id).classList.remove('hidden');
        //     updateGlobalUI();
        // }

        // function goHome() { showPage('landing-page'); }

        // function updateGlobalUI() {
        //     const user = JSON.parse(localStorage.getItem('currentUser'));
        //     const navDash = document.getElementById('nav-dash');
        //     const roleSel = document.getElementById('role-selection');
        //     const sessionMsg = document.getElementById('session-msg');

        //     if (user) {
        //         navDash.classList.remove('hidden');
        //         if (!document.getElementById('landing-page').classList.contains('hidden')) {
        //             roleSel.classList.add('hidden');
        //             sessionMsg.classList.remove('hidden');
        //         }
        //     } else {
        //         navDash.classList.add('hidden');
        //         roleSel.classList.remove('hidden');
        //         sessionMsg.classList.add('hidden');
        //     }
        // }

        // function openLogin(r) { currentAuthRole = r; document.getElementById('login-title').innerText = r.toUpperCase() + " LOGIN"; showPage('login-page'); }

        // function register() {
        //     const u = {
        //         name: document.getElementById('r-name').value,
        //         email: document.getElementById('r-email').value,
        //         pass: document.getElementById('r-pass').value,
        //         role: document.getElementById('r-role').value,
        //         sem: document.getElementById('r-sem').value
        //     };
        //     if (!u.name || !u.email || !u.pass) return alert("Please fill all fields!");
        //     let users = JSON.parse(localStorage.getItem('users') || '[]');
        //     users.push(u);
        //     localStorage.setItem('users', JSON.stringify(users));
        //     alert("Registration Successful!");
        //     showPage('login-page');
        // }

        // function login() {
        //     const email = document.getElementById('l-email').value;
        //     const pass = document.getElementById('l-pass').value;
        //     const users = JSON.parse(localStorage.getItem('users') || '[]');

        //     // Hardcoded admin support check as fallback or use registered ones
        //     const user = users.find(u => u.email === email && u.pass === pass && u.role === currentAuthRole);

        //     if (user) {
        //         localStorage.setItem('currentUser', JSON.stringify(user));
        //         resumeDashboard();
        //     } else if (email === "admin@lams.com" && pass === "admin123" && currentAuthRole === 'admin') {
        //         localStorage.setItem('currentUser', JSON.stringify({ name: 'Super Admin', role: 'admin' }));
        //         loadAdmin();
        //     } else {
        //         alert("Incorrect login details!");
        //     }
        // }

        // function resumeDashboard() {
        //     const user = JSON.parse(localStorage.getItem('currentUser'));
        //     if (user.role === 'student') loadStudent();
        //     else if (user.role === 'teacher') loadTeacher();
        //     else loadAdmin();
        // }

        // function logout() { localStorage.removeItem('currentUser'); location.reload(); }

        // function loadStudent() {
        //     showPage('student-dashboard');
        //     const user = JSON.parse(localStorage.getItem('currentUser'));
        //     document.getElementById('stu-name-tag').innerText = "Student: " + user.name ;
        //     const grid = document.getElementById('sem-grid');
        //     grid.innerHTML = ""; grid.classList.remove('hidden');
        //     document.getElementById('sub-view').classList.add('hidden');
        //     for (let i = 1; i <= 6; i++) grid.innerHTML += `<div class="card" onclick="openSubs(${i})">Semester ${i}</div>`;
        //     updateStudentHistory();
        // }

        // function openSubs(s) {
        //     document.getElementById('sem-grid').classList.add('hidden');
        //     document.getElementById('sub-view').classList.remove('hidden');
        //     const list = document.getElementById('sub-list');
        //     const activeLocs = JSON.parse(localStorage.getItem('active_locs') || '{}');
        //     const atts = JSON.parse(localStorage.getItem('att_data') || '[]');
        //     const user = JSON.parse(localStorage.getItem('currentUser'));

        //     list.innerHTML = `<h3>Available Subjects (Sem ${s})</h3>`;
        //     subjects[s].forEach(sub => {
        //         const last = atts.findLast(a => a.name === user.name && a.sub === sub);
        //         const isLocked = last && (Date.now() - last.ts < 20 * 60 * 60 * 1000);

        //         let statusText = !activeLocs[sub] ? `<span style="color:red">Inactive</span>` :
        //             isLocked ? `<span style="color:var(--teacher)">Locked (20h)</span>` :
        //                 `<button class="btn btn-p" style="width:auto; margin:0" onclick="markAttendance('${sub}', ${s})">Mark Now</button>`;
        //         list.innerHTML += `<div class="list-item"><span>${sub}</span> ${statusText}</div>`;
        //     });
        // }

        // function markAttendance(sub, sem) {
        //     const target = JSON.parse(localStorage.getItem('active_locs'))[sub];
        //     const user = JSON.parse(localStorage.getItem('currentUser'));
        //     navigator.geolocation.getCurrentPosition(pos => {
        //         const dist = calcDist(pos.coords.latitude, pos.coords.longitude, target.lat, target.lng);
        //         if (dist < 100) {
        //             let data = JSON.parse(localStorage.getItem('att_data') || '[]');
        //             data.push({ name: user.name, sem: sem, sub, status: 'Pending', ts: Date.now(), dt: new Date().toLocaleString() });
        //             localStorage.setItem('att_data', JSON.stringify(data));
        //             alert("Attendance request sent!"); loadStudent();
        //         } else alert("Too far! You are " + Math.round(dist) + "m away.");
        //     });
        // }

        // function updateStudentHistory() {
        //     const user = JSON.parse(localStorage.getItem('currentUser'));
        //     const atts = JSON.parse(localStorage.getItem('att_data') || '[]');
        //     const mine = atts.filter(a => a.name === user.name);
        //     document.getElementById('stu-history').innerHTML = mine.reverse().map(a => `
        //         <div class="list-item"><span>${a.sub}<br><small>${a.dt}</small></span><b>${a.status}</b></div>
        //     `).join('') || "No records yet.";
        // }

        // function loadTeacher() {
        //     showPage('teacher-dashboard');
        //     const sel = document.getElementById('t-sub');
        //     const filterSem = document.getElementById('t-filter-sem').value;
        //     sel.innerHTML = Object.values(subjects).flat().map(s => `<option value="${s}">${s}</option>`).join('');

        //     const reqs = JSON.parse(localStorage.getItem('att_data') || '[]');
        //     document.getElementById('teacher-list').innerHTML = reqs.map((r, i) => {
        //         if (r.status === 'Pending' && (filterSem === 'all' || r.sem == filterSem)) {
        //             return `<div class="list-item"><span>${r.name} (Sem ${r.sem}) - ${r.sub}</span><button class="btn btn-p" style="width:auto" onclick="verifyAttendance(${i})">Verify</button></div>`;
        //         }
        //         return '';
        //     }).join('') || "No matching pending requests.";
        // }

        // function verifyAttendance(i) {
        //     let data = JSON.parse(localStorage.getItem('att_data'));
        //     data[i].status = 'Verified';
        //     localStorage.setItem('att_data', JSON.stringify(data));
        //     loadTeacher();
        // }

        // function lockTeacherLoc() {
        //     const sub = document.getElementById('t-sub').value;
        //     navigator.geolocation.getCurrentPosition(pos => {
        //         let locs = JSON.parse(localStorage.getItem('active_locs') || '{}');
        //         locs[sub] = { lat: pos.coords.latitude, lng: pos.coords.longitude };
        //         localStorage.setItem('active_locs', JSON.stringify(locs));
        //         document.getElementById('t-status').innerText = sub + " session ACTIVE! ✅";
        //     });
        // }

        // function loadAdmin() {
        //     showPage('admin-dashboard');
        //     const users = JSON.parse(localStorage.getItem('users') || '[]');
        //     const semFilter = document.getElementById('admin-sem-filter').value;

        //     // Teacher List Column
        //     const teachers = users.filter(u => u.role === 'teacher');
        //     document.getElementById('admin-teacher-list').innerHTML = teachers.map(t =>
        //         `<div class="list-item"><span>${t.name}</span></div>`
        //     ).join('') || "No Teachers Registered";

        //     // Student List Column
        //     const students = users.filter(u => u.role === 'student' && u.sem == semFilter);
        //     document.getElementById('admin-student-list').innerHTML = students.map(s =>
        //         `<div class="list-item"><span>${s.name}</span><small>Sem ${s.sem}</small></div>`
        //     ).join('') || "No Students in this Semester";
        // }

        // function calcDist(l1, n1, l2, n2) {
        //     const R = 6371e3;
        //     const dL = (l2 - l1) * Math.PI / 180; const dN = (n2 - n1) * Math.PI / 180;
        //     const a = Math.sin(dL / 2) ** 2 + Math.cos(l1 * Math.PI / 180) * Math.cos(l2 * Math.PI / 180) * Math.sin(dN / 2) ** 2;
        //     return R * (2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a)));
        // }

        // window.onload = updateGlobalUI;
   
        //updated js
        // --- Global Variables ---
const subjects = { 1:["Computer fundamental & programming", "Fundamental of Management", "Language and communication", "Mathematics-1", "Personal Computer Software"], 2: ["Digital Electronics","Discrete Mathematics","Mathematics-2","Programming in c","Managerial Economics"], 3: ["**[Semester 3]-","Computer oriented numerical analysis", "Computer organization", "Data structure using C", "Object Oriented Programming using C++", "Organization behaviour"], 4: ["**[Semester 4]-","Operating System", "Introduction to DBMS and SQL", "Management Information System", "Visual Basic", "System Analysis and Design"], 5: ["**[Semester 5]-","Computer Graphics And Animation", "Computer network", "Introduction to internet Programming", "Software Engineering", "Advance Computer Architecture"], 6: ["**[Semester 6]-","Multimedia concept and Application", "Artificial Intelligence", "web Technology", "Introduction to .Net"]};
let currentAuthRole = "";

// --- Navigation & UI Logic ---
function showPage(id) {
    document.querySelectorAll('.container > div, #landing-page, #features-page').forEach(p => p.classList.add('hidden'));
    const target = document.getElementById(id);
    if(target) target.classList.remove('hidden');
    updateGlobalUI();
}

function goHome() { showPage('landing-page'); }

function updateGlobalUI() {
    const user = JSON.parse(localStorage.getItem('currentUser'));
    const navDash = document.getElementById('nav-dash');
    const roleSel = document.getElementById('role-selection');
    const sessionMsg = document.getElementById('session-msg');

    if (user) {
        if(navDash) navDash.classList.remove('hidden');
        if (document.getElementById('landing-page') && !document.getElementById('landing-page').classList.contains('hidden')) {
            if(roleSel) roleSel.classList.add('hidden');
            if(sessionMsg) sessionMsg.classList.remove('hidden');
        }
    } else {
        if(navDash) navDash.classList.add('hidden');
        if(roleSel) roleSel.classList.remove('hidden');
        if(sessionMsg) sessionMsg.classList.add('hidden');
    }
}

function openLogin(r) { 
    currentAuthRole = r; 
    const title = document.getElementById('login-title');
    if(title) title.innerText = r.toUpperCase() + " LOGIN"; 
    showPage('login-page'); 
}

function logout() { 
    localStorage.removeItem('currentUser'); 
    location.reload(); 
}

// --- AUTHENTICATION (FLASK BACKEND INTEGRATION) ---

// 1. Send OTP Request
function sendOTPRequest() {
    const data = {
        name: document.getElementById('r-name').value,
        roll_no: document.getElementById('r-roll').value,
        email: document.getElementById('r-email').value,
        pass: document.getElementById('r-pass').value,
        role: document.getElementById('r-role').value,
        sem: document.getElementById('r-sem').value
    };

    if (!data.email || !data.name || !data.pass) {
        return alert("Please fill Name, Email and Password!");
    }

    fetch('/send_otp', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data)
    })
    .then(res => res.json())
    .then(res => {
        if(res.success) {
            alert(res.message);
            document.getElementById('otp-section').classList.remove('hidden');
            document.getElementById('send-otp-btn').classList.add('hidden');
            document.getElementById('reg-final-btn').classList.remove('hidden');
        } else { 
            alert("Error: " + res.message); 
        }
    })
    .catch(err => alert("Server Error: Make sure app.py is running"));
}

// // 2. Verify OTP & Finalize Registration
// function verifyAndRegister() {
//     const otp = document.getElementById('r-otp').value;
//     if(!otp) return alert("Please enter the OTP sent to your email.");

//     fetch('/register', {
//         method: 'POST',
//         headers: {'Content-Type': 'application/json'},
//         body: JSON.stringify({otp: otp})
//     })
//     .then(res => res.json())
//     .then(res => {
//         if(res.success) {
//             alert("Registration Successful! Now you can Login.");
//             showPage('login-page');
//         } else { 
//             alert("Invalid OTP! Please try again."); 
//         }
//     });
// }

// 3. Login Logic
function login() {
    const data = {
        email: document.getElementById('l-email').value,
        pass: document.getElementById('l-pass').value,
        role: currentAuthRole
    };

    if(!data.email || !data.pass) return alert("Enter Email and Password!");

    fetch('/login', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data)
    })
    .then(res => res.json())
    .then(res => {
        if(res.success) {
            localStorage.setItem('currentUser', JSON.stringify(res.user));
            resumeDashboard();
        } else { 
            alert(res.message || "Invalid Login Details!"); 
        }
    });
}

// --- Dashboard Logic ---
function resumeDashboard() {
    const user = JSON.parse(localStorage.getItem('currentUser'));
    if (!user) return showPage('login-page');
    
    if (user.role === 'student') loadStudent();
    else if (user.role === 'teacher') loadTeacher();
    else loadAdmin();
}

function loadStudent() {
    showPage('student-dashboard');
    const user = JSON.parse(localStorage.getItem('currentUser'));
    document.getElementById('stu-name-tag').innerText = "Student: " + user.name + (user.roll_no ? ` (${user.roll_no})` : "");
    const grid = document.getElementById('sem-grid');
    grid.innerHTML = ""; 
    grid.classList.remove('hidden');
    document.getElementById('sub-view').classList.add('hidden');
    for (let i = 1; i <= 6; i++) {
        grid.innerHTML += `<div class="card" onclick="openSubs(${i})">Semester ${i}</div>`;
    }
}

// Helper: Registration Role Toggle
if(document.getElementById('r-role')) {
    document.getElementById('r-role').addEventListener('change', function () {
        const semSelect = document.getElementById('r-sem');
        const rollInput = document.getElementById('r-roll');
        if(this.value === 'student') {
            semSelect.classList.remove('hidden');
            if(rollInput) rollInput.style.display = "block";
        } else {
            semSelect.classList.add('hidden');
            if(rollInput) rollInput.style.display = "none";
        }
    });
}

window.onload = updateGlobalUI;