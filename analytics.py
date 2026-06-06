import os
import json
import csv

BASE_DIR = os.getcwd()
DATASET_PATH = os.path.join(BASE_DIR, "dataset")
REGISTRY_PATH = os.path.join(DATASET_PATH, "registry.json")
SESSION_ROOT = os.path.join(BASE_DIR, "sessions")
SESSION_DB = os.path.join(SESSION_ROOT, "sessions.json")

def resolve_folder_path(folder):
    if not folder:
        return ""
    # Normalize slashes to current OS platform
    folder_norm = os.path.normpath(folder.replace("\\", "/"))
    if os.path.exists(folder_norm):
        return folder_norm
        
    # Try resolving relative path if it contains 'sessions'
    normalized_path_str = folder.replace("\\", "/")
    if "sessions/" in normalized_path_str:
        parts = normalized_path_str.split("sessions/", 1)
        rel_part = os.path.join("sessions", parts[1].replace("/", os.sep))
        abs_resolved = os.path.normpath(os.path.join(BASE_DIR, rel_part))
        if os.path.exists(abs_resolved):
            return abs_resolved
            
    return folder_norm

def load_registry():
    if not os.path.exists(REGISTRY_PATH):
        return {}
    try:
        with open(REGISTRY_PATH, "r") as f:
            content = f.read().strip()
            return json.loads(content) if content else {}
    except Exception:
        return {}

def load_sessions():
    if not os.path.exists(SESSION_DB):
        return []
    try:
        with open(SESSION_DB, "r") as f:
            return json.load(f)
    except Exception:
        return []

def get_analytics_data():
    registry = load_registry()
    sessions = load_sessions()

    total_students = len(registry)
    total_sessions = len(sessions)

    # Initialize stats
    student_attendance = {
        usn: {"present": 0, "total": 0, "name": info.get("name", usn)} 
        for usn, info in registry.items()
    }

    subject_attendance = {}
    timeline_data = []

    for s in sessions:
        subject = s.get("subject", "Other")
        folder = s.get("folder")
        time_str = s.get("time", "")  # format: YYYY-MM-DD HH:MM
        
        resolved_folder = resolve_folder_path(folder)
        if not resolved_folder or not os.path.exists(resolved_folder):
            continue

        csv_path = os.path.normpath(os.path.join(resolved_folder, "attendance.csv"))
        if not os.path.exists(csv_path):
            continue

        try:
            with open(csv_path, "r") as f:
                reader = csv.reader(f)
                header = next(reader, None)  # Skip header: USN, Name, Time, Status
                
                present_in_session = 0
                total_in_session = 0

                for row in reader:
                    if len(row) < 4:
                        continue
                    usn, name, time_val, status = row[0], row[1], row[2], row[3]
                    is_present = (status.strip().lower() == "present")

                    # Student tracking
                    if usn not in student_attendance:
                        student_attendance[usn] = {"present": 0, "total": 0, "name": name}
                    student_attendance[usn]["total"] += 1
                    if is_present:
                        student_attendance[usn]["present"] += 1

                    # Subject tracking
                    if subject not in subject_attendance:
                        subject_attendance[subject] = {"present": 0, "total": 0}
                    subject_attendance[subject]["total"] += 1
                    if is_present:
                        subject_attendance[subject]["present"] += 1

                    # Session tracking
                    total_in_session += 1
                    if is_present:
                        present_in_session += 1

                if total_in_session > 0:
                    timeline_data.append({
                        "date": time_str,
                        "subject": subject,
                        "rate": round((present_in_session / total_in_session) * 100, 1)
                    })
        except Exception as e:
            print(f"⚠️ Error parsing CSV {csv_path}: {e}")

    # Compute overall statistics
    total_p = sum(s["present"] for s in student_attendance.values())
    total_t = sum(s["total"] for s in student_attendance.values())
    overall_rate = round((total_p / total_t) * 100, 1) if total_t > 0 else 0.0

    # Compute subject percentages
    subject_rates = {}
    for sub, counts in subject_attendance.items():
        subject_rates[sub] = round((counts["present"] / counts["total"]) * 100, 1) if counts["total"] > 0 else 0.0

    # Compute low attendance lists (< 75%)
    low_attendance = []
    for usn, counts in student_attendance.items():
        if counts["total"] > 0:
            rate = (counts["present"] / counts["total"]) * 100
            if rate < 75.0:
                low_attendance.append({
                    "usn": usn,
                    "name": counts["name"],
                    "rate": round(rate, 1),
                    "attended": counts["present"],
                    "total": counts["total"]
                })
        else:
            # Enrolled but absent in all recorded sessions
            low_attendance.append({
                "usn": usn,
                "name": counts["name"],
                "rate": 0.0,
                "attended": 0,
                "total": 0
            })

    # Sort low attendance list by worst rates first
    low_attendance.sort(key=lambda x: x["rate"])

    # Limit timeline data to last 8 sessions for clean visual display
    timeline_data = timeline_data[-8:]

    return {
        "total_students": total_students,
        "total_sessions": total_sessions,
        "overall_rate": overall_rate,
        "subject_rates": subject_rates,
        "low_attendance": low_attendance,
        "timeline_data": timeline_data
    }

def generate_insights_html():
    data = get_analytics_data()
    
    # Format data values for JavaScript
    subjects = list(data["subject_rates"].keys())
    rates = list(data["subject_rates"].values())
    
    timeline_dates = [t["date"] for t in data["timeline_data"]]
    timeline_rates = [t["rate"] for t in data["timeline_data"]]

    # Construct Low Attendance Table rows
    table_rows_html = ""
    if data["low_attendance"]:
        for item in data["low_attendance"]:
            table_rows_html += f"""
            <tr style="border-bottom: 1px solid rgba(198, 40, 40, 0.15);">
                <td style="padding: 12px; font-weight: 600; color: #c62828;">{item['usn']}</td>
                <td style="padding: 12px;">{item['name']}</td>
                <td style="padding: 12px; font-weight: 700; color: #c62828;">{item['rate']}%</td>
                <td style="padding: 12px; text-align: center;">{item['attended']} / {item['total']}</td>
            </tr>
            """
    else:
        table_rows_html = """
        <tr>
            <td colspan="4" style="padding: 30px; text-align: center; color: #2e7d32; font-weight: 600; font-size: 1.05rem;">
                ✨ Perfect Attendance Health! No registered student is below 75% threshold.
            </td>
        </tr>
        """

    # Premium Dashboard HTML Template
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            .analytics-wrapper {{
                font-family: 'Outfit', 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
                margin: 0;
                padding: 0;
            }}
            
            /* KPI GRID */
            .kpi-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
                gap: 20px;
                margin-bottom: 30px;
            }}
            .kpi-card {{
                background: #ffffff;
                border: 1px solid rgba(90, 31, 34, 0.08);
                border-radius: 16px;
                padding: 24px;
                box-shadow: 0 4px 12px rgba(90, 31, 34, 0.03);
                transition: transform 0.25s ease, box-shadow 0.25s ease;
            }}
            .kpi-card:hover {{
                transform: translateY(-2px);
                box-shadow: 0 8px 24px rgba(90, 31, 34, 0.08);
            }}
            .kpi-label {{
                font-size: 0.82rem;
                font-weight: 700;
                color: #5a1f22;
                opacity: 0.7;
                text-transform: uppercase;
                letter-spacing: 0.5px;
                margin-bottom: 8px;
            }}
            .kpi-value {{
                font-size: 2.2rem;
                font-weight: 800;
                color: #5a1f22;
                margin: 0;
            }}
            .kpi-subtext {{
                font-size: 0.85rem;
                color: #666;
                margin-top: 6px;
            }}
            
            /* CHARTS GRID */
            .charts-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
                gap: 25px;
                margin-bottom: 30px;
            }}
            .chart-card {{
                background: #ffffff;
                border: 1px solid rgba(90, 31, 34, 0.08);
                border-radius: 16px;
                padding: 24px;
                box-shadow: 0 4px 12px rgba(90, 31, 34, 0.03);
                min-height: 320px;
            }}
            .chart-title {{
                font-size: 1.1rem;
                font-weight: 700;
                color: #5a1f22;
                margin-top: 0;
                margin-bottom: 20px;
                border-bottom: 1px solid rgba(90, 31, 34, 0.08);
                padding-bottom: 10px;
            }}
            
            /* TABLE STYLING */
            .warning-card {{
                background: rgba(198, 40, 40, 0.03);
                border: 1.5px solid rgba(198, 40, 40, 0.2);
                border-radius: 16px;
                padding: 24px;
                box-shadow: 0 4px 12px rgba(198, 40, 40, 0.04);
            }}
            .warning-title {{
                font-size: 1.1rem;
                font-weight: 700;
                color: #c62828;
                margin-top: 0;
                margin-bottom: 18px;
                display: flex;
                align-items: center;
                gap: 8px;
            }}
            .warning-table {{
                width: 100%;
                border-collapse: collapse;
                text-align: left;
            }}
            .warning-table th {{
                background: rgba(198, 40, 40, 0.08);
                color: #c62828;
                font-weight: 700;
                font-size: 0.85rem;
                text-transform: uppercase;
                letter-spacing: 0.5px;
                padding: 10px 12px;
            }}
            
            /* DARK THEME STYLES - Inherits from Gradio */
            @media (prefers-color-scheme: dark) {{
                .kpi-card, .chart-card {{
                    background: rgba(61, 20, 22, 0.55);
                    border-color: rgba(229, 195, 101, 0.15);
                }}
                .kpi-label {{
                    color: #e5c365;
                    opacity: 0.8;
                }}
                .kpi-value, .chart-title {{
                    color: #fce594;
                }}
                .kpi-subtext {{
                    color: rgba(252, 229, 148, 0.7);
                }}
                .chart-title {{
                    border-color: rgba(229, 195, 101, 0.15);
                }}
                .warning-card {{
                    background: rgba(198, 40, 40, 0.08);
                    border-color: rgba(198, 40, 40, 0.4);
                }}
            }}
            
            /* Gradio custom selector support for dark theme */
            .dark-mode-override .kpi-card, .dark-mode-override .chart-card {{
                background: rgba(61, 20, 22, 0.55) !important;
                border-color: rgba(229, 195, 101, 0.15) !important;
            }}
            .dark-mode-override .kpi-label {{
                color: #e5c365 !important;
                opacity: 0.8 !important;
            }}
            .dark-mode-override .kpi-value, .dark-mode-override .chart-title {{
                color: #fce594 !important;
            }}
            .dark-mode-override .kpi-subtext {{
                color: rgba(252, 229, 148, 0.7) !important;
            }}
            .dark-mode-override .chart-title {{
                border-color: rgba(229, 195, 101, 0.15) !important;
            }}
            .dark-mode-override .warning-card {{
                background: rgba(198, 40, 40, 0.08) !important;
                border-color: rgba(198, 40, 40, 0.4) !important;
            }}
        </style>
    </head>
    <body>
        <div class="analytics-wrapper" id="themeContainer">
            
            <!-- KPI METRICS ROW -->
            <div class="kpi-grid">
                <div class="kpi-card">
                    <div class="kpi-label">Registered Students</div>
                    <h2 class="kpi-value">{data['total_students']}</h2>
                    <div class="kpi-subtext">Baseline enrollment from registry</div>
                </div>
                <div class="kpi-card">
                    <div class="kpi-label">Sessions Recorded</div>
                    <h2 class="kpi-value">{data['total_sessions']}</h2>
                    <div class="kpi-subtext">Total lectures logged chronologically</div>
                </div>
                <div class="kpi-card">
                    <div class="kpi-label">Average Attendance</div>
                    <h2 class="kpi-value" style="color: { '#2e7d32' if data['overall_rate'] >= 75 else '#c62828' };">
                        {data['overall_rate']}%
                    </h2>
                    <div class="kpi-subtext">Global attendance rate across all subjects</div>
                </div>
            </div>
            
            <!-- CHARTS CONTAINER -->
            <div class="charts-grid">
                <div class="chart-card">
                    <h3 class="chart-title">📚 Attendance by Subject</h3>
                    <div style="position: relative; height:240px;">
                        <canvas id="subjectChart"></canvas>
                    </div>
                </div>
                <div class="chart-card">
                    <h3 class="chart-title">📈 Attendance Trends (Over Time)</h3>
                    <div style="position: relative; height:240px;">
                        <canvas id="trendChart"></canvas>
                    </div>
                </div>
            </div>
            
            <!-- WARNING TABLE CONTAINER -->
            <div class="warning-card">
                <h3 class="warning-title">⚠️ Attendance Risk Alert (Below 75% Threshold)</h3>
                <div style="overflow-x: auto;">
                    <table class="warning-table">
                        <thead>
                            <tr>
                                <th>USN</th>
                                <th>Student Name</th>
                                <th>Attendance Rate</th>
                                <th style="text-align: center;">Attended Sessions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {table_rows_html}
                        </tbody>
                    </table>
                </div>
            </div>
            
        </div>
        
        <script>
            (function() {{
                let attempts = 0;
                const maxAttempts = 100; // Try for 10 seconds

                function initCharts() {{
                    const subjectCanvas = document.getElementById('subjectChart');
                    const trendCanvas = document.getElementById('trendChart');

                    // If elements or library are not ready yet, retry in 100ms
                    if (typeof Chart === 'undefined' || !subjectCanvas || !trendCanvas) {{
                        attempts++;
                        if (attempts < maxAttempts) {{
                            setTimeout(initCharts, 100);
                        }} else {{
                            console.error("Chart.js failed to load or canvas elements not found.");
                            if (subjectCanvas) {{
                                subjectCanvas.parentElement.innerHTML = "<p style='color:#c62828; padding:20px; font-weight:600;'>⚠️ Unable to load charts. Please check your internet connection to load Chart.js from CDN.</p>";
                            }}
                        }}
                        return;
                    }}

                    // Setup theme detection
                    function updateTheme() {{
                        const isDark = document.documentElement.classList.contains('dark') || 
                                       document.body.classList.contains('dark') ||
                                       window.matchMedia('(prefers-color-scheme: dark)').matches;
                        
                        const container = document.getElementById('themeContainer');
                        if (container) {{
                            if (isDark) {{
                                container.classList.add('dark-mode-override');
                            }} else {{
                                container.classList.remove('dark-mode-override');
                            }}
                        }}
                        return isDark;
                    }}
                    
                    const isDark = updateTheme();
                    const primaryColor = '#5a1f22';
                    const secondaryColor = '#e5c365';
                    const gridColor = isDark ? 'rgba(255, 255, 255, 0.08)' : 'rgba(0, 0, 0, 0.05)';
                    const labelColor = isDark ? '#fce594' : '#331515';

                    try {{
                        // ── SUBJECT CHART ──
                        const subCtx = subjectCanvas.getContext('2d');
                        new Chart(subCtx, {{
                            type: 'bar',
                            data: {{
                                labels: {json.dumps(subjects)},
                                datasets: [{{
                                    label: 'Attendance %',
                                    data: {json.dumps(rates)},
                                    backgroundColor: [
                                        'rgba(90, 31, 34, 0.85)',
                                        'rgba(229, 195, 101, 0.85)',
                                        'rgba(198, 40, 40, 0.85)',
                                        'rgba(46, 125, 50, 0.85)',
                                        'rgba(120, 144, 156, 0.85)'
                                    ],
                                    borderColor: isDark ? '#e5c365' : '#5a1f22',
                                    borderWidth: 1.5,
                                    borderRadius: 6
                                }}]
                            }},
                            options: {{
                                responsive: true,
                                maintainAspectRatio: false,
                                plugins: {{
                                    legend: {{ display: false }}
                                }},
                                scales: {{
                                    y: {{
                                        beginAtZero: true,
                                        max: 100,
                                        grid: {{ color: gridColor }},
                                        ticks: {{ color: labelColor }}
                                    }},
                                    x: {{
                                        grid: {{ display: false }},
                                        ticks: {{ color: labelColor }}
                                    }}
                                }}
                            }}
                        }});
                        
                        // ── TREND CHART ──
                        const trendCtx = trendCanvas.getContext('2d');
                        new Chart(trendCtx, {{
                            type: 'line',
                            data: {{
                                labels: {json.dumps(timeline_dates)},
                                datasets: [{{
                                    label: 'Attendance Rate %',
                                    data: {json.dumps(timeline_rates)},
                                    borderColor: '#e5c365',
                                    backgroundColor: 'rgba(229, 195, 101, 0.15)',
                                    borderWidth: 3,
                                    pointBackgroundColor: '#5a1f22',
                                    pointBorderColor: '#e5c365',
                                    pointHoverRadius: 7,
                                    tension: 0.35,
                                    fill: true
                                }}]
                            }},
                            options: {{
                                responsive: true,
                                maintainAspectRatio: false,
                                plugins: {{
                                    legend: {{ display: false }}
                                }},
                                scales: {{
                                    y: {{
                                        beginAtZero: true,
                                        max: 100,
                                        grid: {{ color: gridColor }},
                                        ticks: {{ color: labelColor }}
                                    }},
                                    x: {{
                                        grid: {{ display: false }},
                                        ticks: {{ color: labelColor }}
                                    }}
                                }}
                            }}
                        }});

                        // Re-check theme on document adjustments
                        setInterval(updateTheme, 1500);

                    }} catch (e) {{
                        console.error("Error drawing charts: ", e);
                    }}
                }}

                // Start searching for DOM elements and library
                initCharts();
            }})();
        </script>
    </body>
    </html>
    """
    return html_content
