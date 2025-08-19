# 📑 Crossref Preprints Metadata Collector

This Streamlit app allows you to **check if preprint servers are present in Crossref**, explore metadata, and collect information about them.

## 🚀 What the app does
1. **Input preprint servers**  
   - Upload a CSV file (one server name per line) or  
   - Paste/type names manually.

2. **Resolve server names**  
   - The app queries the Crossref API to check if the servers are present.  
   - If multiple candidates are found, you can select the correct one(s).  

3. **Collect metadata**  
   - For each selected server, the app retrieves metadata such as:  
     - Server ID and name  
     - DOI prefix  
     - Publisher details  
     - Works count  
     - Other available metadata fields  

4. **Raw metadata preview**  
   - You can preview the **raw JSON metadata** for one sample preprint per server (expandable panel).  
   - This helps you inspect exactly what Crossref provides.

5. **Download results**  
   - The collected server metadata is exported to **servers.csv**.  
   - A ZIP archive containing the CSV file and JSON samples is available to download.

---

## 📦 Output files
- `servers.csv` → one row per server with key metadata.  
- `json/` folder inside the ZIP → contains raw JSON sample files.  

---

## 🛠️ How to run locally
1. Clone the repository:
   ```bash
   git clone https://github.com/your-username/crossref-preprints-metadata.git
   cd crossref-preprints-metadata
   ```

2. Create a virtual environment (optional but recommended):
   ```bash
   python -m venv .venv
   source .venv/bin/activate   # on Linux/Mac
   .venv\Scripts\activate    # on Windows
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Run the app:
   ```bash
   streamlit run streamlit_crossref_preprints_app.py
   ```

---

## 🌐 Deployment
You can easily deploy this app on [Streamlit Cloud](https://streamlit.io/cloud):
1. Push your repo (with `requirements.txt` and `streamlit_crossref_preprints_app.py`) to GitHub.  
2. Go to [Streamlit Cloud](https://streamlit.io/cloud), create a new app, and connect your repo.  
3. Select the Python file `streamlit_crossref_preprints_app.py` as the entry point.  

---

## 💡 Tips
- Crossref metadata coverage varies; not all preprint servers may appear.  
- JSON previews are helpful for exploring data structures before downstream analysis.  
- This app focuses on **metadata only** (no yearly or monthly trend aggregation like in the OpenAlex app).  

---

## 📬 Contact
If you have questions or suggestions, feel free to open an issue or reach out!
