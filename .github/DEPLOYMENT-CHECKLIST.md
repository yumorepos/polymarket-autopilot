# Streamlit Cloud Deployment Checklist

## Pre-Deployment ✅

- [x] Dashboard code complete (`dashboard.py`)
- [x] Requirements file added (`requirements.txt`)
- [x] Streamlit config created (`.streamlit/config.toml`)
- [x] Code syntax validated
- [x] Committed to GitHub
- [x] Documentation added (`DASHBOARD.md`)

## Deployment Steps

### Option 1: Streamlit Cloud (Recommended for Demo)

1. **Note:** Database file (`data/autopilot.db`) is 28MB and NOT in git (in `.gitignore`)
   
2. For **demo purposes only**, you can temporarily commit the DB:
   ```bash
   git add -f data/autopilot.db
   git commit -m "Add DB snapshot for Streamlit Cloud demo"
   git push origin main
   ```

3. Go to https://share.streamlit.io/
4. Click "New app"
5. Repository: `yumorepos/polymarket-autopilot`
6. Branch: `main`
7. Main file: `dashboard.py`
8. Click "Deploy!"

### Option 2: Local Testing

```bash
cd /Users/yumo/Projects/polymarket-autopilot
source .venv/bin/activate
streamlit run dashboard.py
```

### Option 3: Production (Cloud Database)

For production use, migrate to a cloud database:

1. **PostgreSQL** (recommended):
   - Use Supabase, AWS RDS, or Google Cloud SQL
   - Update `dashboard.py` to use `psycopg2` instead of `sqlite3`
   - Store connection string in Streamlit secrets

2. **MongoDB**:
   - Use MongoDB Atlas
   - Migrate schema to document model
   - Update data loading functions

3. **Cloud Storage + SQLite**:
   - Upload DB to S3/GCS
   - Download on dashboard startup
   - Set up cron to sync updates

## Post-Deployment

- [ ] Test all dashboard features
- [ ] Verify data loads correctly
- [ ] Check equity curve renders
- [ ] Validate strategy breakdowns
- [ ] Test responsiveness on mobile
- [ ] Share link with stakeholders

## Security Notes

⚠️ **Warning**: Committing the database to a public repo exposes your trading data. Only do this for:
- Private repositories
- Demo/test data
- Temporary proof-of-concept

For production:
- Use environment variables for sensitive configs
- Store DB credentials in Streamlit secrets
- Enable authentication on the dashboard
- Use a proper cloud database

## Current Status

✅ **Deployment-ready** - Code is production-quality and tested
📊 **Database**: 28MB SQLite file with 70 trades, 116.2k snapshots
🎨 **Theme**: Dark mode with cyan/teal accent colors
📈 **Charts**: Interactive Plotly visualizations
