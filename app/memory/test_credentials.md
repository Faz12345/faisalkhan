
Action: file_editor create /app/memory/test_credentials.md --file-text "# Sparkle Dental — Test Credentials

## Doctor (seeded on startup)
- Email: `shahzad@dentalclinic.com`
- Password: `doctor123`
- Role: doctor
- Login: `POST /api/login`
- Landing after login: `/api/doctor`

## Patient (create via UI)
- Register at `/api/register` (email + password ≥ 6 chars)
- Landing after login: `/api/my-appointments`

## Key routes
- `/api/` — home
- `/api/about` — about the doctor
- `/api/login` — login (form)
- `/api/register` — patient signup (form)
- `/api/logout` — POST
- `/api/book` — book appointment (patient)
- `/api/my-appointments` — patient dashboard
- `/api/doctor` — doctor dashboard
- `/api/doctor/patients` — patient list
- `/api/doctor/patient/{id}` — patient detail (add treatment notes)

Auth cookie: `dental_token` (httpOnly, JWT, 7 days).
"
Observation: Failed to create file: File already exists at: /app/memory/test_credentials.md. Use overwrite=True to replace
