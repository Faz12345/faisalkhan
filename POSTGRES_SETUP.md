# PostgreSQL setup and MongoDB migration

This app now stores its existing document-shaped records in PostgreSQL `jsonb`
tables. Existing MongoDB ObjectId values are retained as 24-character IDs, so
user references and login tokens continue to work after the import.

## Create a free PostgreSQL database

The steps below use Neon; free-plan limits and availability can change, so
confirm current terms in the Neon console.

1. Sign up at [neon.com](https://neon.com/) and create a project on the free
   plan. Select a region close to your app's hosting region.
2. In the project, choose **Connect**. Select the branch, database, and role.
   Keep **Connection pooling** enabled for this web app.
3. Copy the full PostgreSQL connection string. Keep it private; do not commit
   it to Git or paste it into source files.
4. Set `DATABASE_URL` to that connection string in `app/backend/.env` for local
   development. Add the same variable to the hosting provider's environment
   settings for each deployed environment. For Vercel, add it under the
   project's **Settings → Environment Variables** and redeploy.

The app creates its PostgreSQL tables and indexes on startup. Keep the existing
`JWT_SECRET`, `DOCTOR_EMAIL`, `DOCTOR_PASSWORD`, and `DOCTOR_NAME` settings too.

## Copy existing MongoDB data

1. Back up the MongoDB database before starting.
2. Temporarily ensure `MONGO_URL` and `DB_NAME` still point to the source
   MongoDB database, and set `DATABASE_URL` to the new PostgreSQL connection
   string in `app/backend/.env`. Do not remove the Mongo settings until the
   import has completed and the new deployment has been checked.
3. Install the project requirements, then run this from the repository root:

   ```powershell
   pip install -r requirements.txt
   python app\backend\migrate_mongo_to_postgres.py
   ```

4. The importer reports counts only. It copies `users`, `appointments`,
   `treatments`, and `notifications`, retaining IDs and references. It can be
   rerun: records with the same IDs are updated from MongoDB. Resolve any
   import error before switching the live app.
5. Deploy the app with `DATABASE_URL` configured. Confirm login, booking,
   patient history, and notification behavior. Once verified, remove
   `MONGO_URL` and `DB_NAME` from the deployed app's environment settings.

The importer needs MongoDB access only during the copy. The running app uses
PostgreSQL and does not connect to MongoDB.
