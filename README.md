# AI Easy Bets Engine
**🌐 Live Site:** [easybets.com](http://13.60.66.250:8000/) 

EasyBets Engine 🎯
An autonomous, AI-driven quantitative analytics platform that tracks over 300+ live sports prediction markets daily. EasyBets utilizes a multi-agent LLM pipeline to calculate objective "Fair Value," identify statistical mispricings, and automatically track historical ROI.

🚀 Overview
Prediction markets move fast, and finding true statistical edges requires synthesizing real-time news, historical data, and live order books. EasyBets automates this process. By integrating the Polymarket Gamma API with Google's Gemini and Tavily's live web search, the engine evaluates real-time odds, generates bull/bear cases, and logs verifiable "BUY" predictions when a high-confidence edge is found.

✨ Key Features
Real-Time Market Scanning: Ingests and filters 300+ live sports markets daily from Polymarket.

AI Edge Calculation: Uses Gemini to synthesize live web data (via Tavily) to calculate true probability ("Fair Value") and compare it against the current market YES/NO price.

Automated Resolution Tracking: Background workers autonomously track pending bets, verifying live API endpoints to settle wins/losses only when the market officially closes.

Track Record Dashboard: A responsive SPA frontend that visualizes cumulative win rates, overall ROI, and pending vs. resolved calls.

Custom User Alerts: Users can set specific price targets (e.g., "Alert me if YES drops below 40¢"), monitored by a dedicated background worker.

🛠 Tech Stack
Backend: Python, FastAPI

Frontend: Vanilla JavaScript, HTML/CSS (Single Page Application)

Database: MongoDB

Deployment: AWS EC2 (Ubuntu), Uvicorn, Tmux

AI & APIs: Gemini API (LLM), Tavily API (Live Search), Polymarket Gamma API

🧠 System Architecture
The system is broken down into three main parallel processes:

The Core API (uvicorn api.main:app): Serves the frontend application, handles on-demand AI market analysis, and manages database reads/writes for the Track Record.

The Prediction Worker (prediction_worker.py): A daemonized script that runs twice a day to check the official end dates of pending markets, automatically settling them in the database when they conclude.

The Alert Worker (alert_worker.py): A background loop that continuously monitors live prices against user-defined thresholds, triggering alerts when actionable lines are crossed.

💻 Getting Started
Prerequisites
Python 3.8+

MongoDB database (local or Atlas)

API Keys for Gemini and Tavily

Installation
Clone the repository:

Bash
git clone https://github.com/yourusername/easy-bets-engine.git
cd easy-bets-engine
Set up the virtual environment:

Bash
python3 -m venv venv
source venv/bin/activate
Install dependencies:

Bash
pip install -r requirements.txt
Environment Variables:
Create a .env file in the root directory and add your keys:

Code snippet
GEMINI_API_KEY=your_gemini_key
TAVILY_API_KEY=your_tavily_key
MONGO_URI=your_mongodb_connection_string
Run the application:

Bash
# Start the web server
uvicorn api.main:app --reload

# In separate terminal windows (or tmux sessions), start the background workers:
python3 prediction_worker.py
python3 alert_worker.py