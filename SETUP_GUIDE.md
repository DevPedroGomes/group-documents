# Complete Setup Guide: Team Hub

This guide covers the complete configuration of your **Team Hub** application with Email/Password and Google OAuth authentication.

---

## 1. Prerequisites

1. **Node.js 18+** and **Python 3.10+**
2. **Supabase** Account - [supabase.com](https://supabase.com)
3. **Google AI Studio** API Key - [aistudio.google.com](https://aistudio.google.com/)
4. **Google Cloud Console** Account (for Google OAuth) - [console.cloud.google.com](https://console.cloud.google.com/)

---

## 2. Supabase Setup (Database & Storage)

### Step A: Create Project
1. Go to [supabase.com/dashboard](https://supabase.com/dashboard)
2. Click "New Project"
3. Choose an organization and set a name/password
4. Select a region close to your users

### Step B: Database Schema (SQL)
1. Go to **SQL Editor** > **New Query**
2. Copy and execute the content of: `sql/schema_complete.sql`

> **What does this do?**
> - Enables `pgvector` extension
> - Creates tables `documents`, `chunks` (with 768-dimension vectors for Gemini), and `threads`
> - Configures Row Level Security (RLS) policies for **Team Mode** (shared files, private chats)

### Step C: Storage Bucket
1. Go to **Storage** > **New Bucket**
2. Name: `docs`
3. Leave "Public bucket" **UNCHECKED**
4. Create the following policies for the `docs` bucket:
   - **SELECT**: Allow for `authenticated` users
   - **INSERT**: Allow for `authenticated` users
   - **DELETE**: Allow for `authenticated` users

---

## 3. Supabase Authentication Setup

### Step A: Configure Redirect URLs

1. Go to **Authentication** > **URL Configuration**
2. Set **Site URL**: `http://localhost:3000`
3. Add to **Redirect URLs**:

**Development:**
```
http://localhost:3000/auth/callback
http://127.0.0.1:3000/auth/callback
```

**Production (add when deploying):**
```
https://your-domain.com/auth/callback
https://www.your-domain.com/auth/callback
```

> The application code uses `window.location.origin` dynamically, so it automatically works in any environment. You just need to whitelist the URLs in Supabase.

### Step B: Configure Google OAuth Provider

To enable "Continue with Google" login:

#### 1. Access Google Cloud Console
Go to [console.cloud.google.com](https://console.cloud.google.com/)

#### 2. Create a Project (or use existing)
- Click "Select a project" > "New Project"
- Name it (e.g., "Document Hub")
- Click "Create"

#### 3. Configure OAuth Consent Screen
- Go to **APIs & Services** > **OAuth consent screen**
- Choose **"External"** (allows any Google user)
- Fill in:
  - **App name**: "Document Hub" (or your app name)
  - **User support email**: your email
  - **Developer contact email**: your email
- Click "Save and Continue" through the remaining steps
- No need to add scopes for basic authentication

#### 4. Create OAuth Credentials
- Go to **APIs & Services** > **Credentials**
- Click **"+ Create Credentials"** > **"OAuth client ID"**
- Select **Application type**: "Web application"
- **Name**: "Document Hub Web" (or any name)
- **Authorized redirect URIs** - Add:
  ```
  https://<YOUR-PROJECT-REF>.supabase.co/auth/v1/callback
  ```
  (Replace `<YOUR-PROJECT-REF>` with your Supabase project reference, e.g., `hdwtzfpvprhswwlccefl`)
- Click **"Create"**

#### 5. Copy the Generated Values
Google will display:
- **Client ID**: looks like `123456789-abc123xyz.apps.googleusercontent.com`
- **Client Secret**: looks like `GOCSPX-AbCdEfGhIjKlMnOp`

**Save these values!**

#### 6. Configure in Supabase
- Go to **Authentication** > **Providers**
- Find **Google** and toggle it ON
- Paste the **Client ID** and **Client Secret**
- Click **Save**

---

## 4. Environment Variables

### Backend (`backend/.env`)

```bash
# Supabase (Settings > API)
SUPABASE_URL="https://your-project.supabase.co"
SUPABASE_ANON_KEY="your-public-anon-key"
SUPABASE_SERVICE_ROLE_KEY="your-secret-service-role-key"
SUPABASE_JWT_SECRET="your-jwt-secret"
SUPABASE_DB_URL="postgresql://postgres.xxx:password@aws-0-region.pooler.supabase.com:6543/postgres"

# Google Gemini (AI Brain)
GOOGLE_API_KEY="your-google-ai-studio-key"

# Backend Settings
CORS_ORIGINS="http://localhost:3000"
STORAGE_BUCKET="docs"
SIM_THRESHOLD="0.2"
```

### Frontend (`frontend/.env.local`)

```bash
NEXT_PUBLIC_SUPABASE_URL="https://your-project.supabase.co"
NEXT_PUBLIC_SUPABASE_ANON_KEY="your-public-anon-key"
NEXT_PUBLIC_API_URL="http://localhost:8000"
```

### Where to Find Supabase Credentials

| Credential | Location in Dashboard |
|------------|----------------------|
| Project URL | Settings > API > Project URL |
| Anon Key | Settings > API > Project API Keys > `anon` `public` |
| Service Role Key | Settings > API > Project API Keys > `service_role` `secret` |
| JWT Secret | Settings > API > JWT Settings > JWT Secret |
| Database URL | Settings > Database > Connection string > URI |

---

## 5. Running the Project

### Backend (Python/FastAPI)

```bash
cd backend

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run server
uvicorn app.main:app --reload --port 8000
```

### Frontend (Next.js)

```bash
cd frontend

# Install dependencies
npm install

# Run development server
npm run dev
```

### Access the Application

- **Frontend**: http://localhost:3000
- **Backend API**: http://localhost:8000
- **API Docs (Swagger)**: http://localhost:8000/docs

---

## 6. Authentication Features

The application supports three authentication methods:

### Email/Password Login
1. Enter email and password
2. Click "Sign In"
3. Instant login (no email verification required by default)

### Email/Password Signup
1. Click "Sign up" link
2. Enter email and password (min 6 characters)
3. Click "Create Account"
4. Account created immediately

### Google OAuth (One-Click)
1. Click "Continue with Google"
2. Select your Google account
3. Authorize the application
4. Redirected back and logged in

---

## 7. Testing the Application

1. **Open** http://localhost:3000
2. **Create an account** or **Sign in with Google**
3. **Upload** a PDF, Image, or Video (drag & drop or click "+ New File")
4. Wait for processing status to complete
5. **Select** the file by clicking on the card
6. **Ask questions** in the chat: "What is this document about?"

---

## 8. Production Deployment Checklist

When deploying to production:

- [ ] Add production URLs to Supabase Redirect URLs
- [ ] Add production redirect URI to Google Cloud Console
- [ ] Update `CORS_ORIGINS` in backend to include production domain
- [ ] Set `NEXT_PUBLIC_API_URL` to production backend URL
- [ ] Enable email confirmation in Supabase if desired (Authentication > Settings)
- [ ] Consider adding rate limiting and additional security measures

---

## Troubleshooting

### "Invalid login credentials"
- Check if the user exists (try signing up first)
- Verify password meets minimum requirements (6+ characters)

### Google OAuth not working
- Verify Client ID and Secret are correctly pasted in Supabase
- Check that redirect URI in Google Console matches exactly:
  `https://<PROJECT-REF>.supabase.co/auth/v1/callback`
- Ensure Google provider is enabled in Supabase

### 401 Unauthorized errors
- Check that `SUPABASE_JWT_SECRET` is set correctly in backend
- Verify the user is logged in and token is being sent

### CORS errors
- Ensure `CORS_ORIGINS` includes your frontend URL
- Check that the URL doesn't have a trailing slash
