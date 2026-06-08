/* ─────────────────────────────────────────────────────────────────────────
   SUPABASE CONFIG  —  paste your 2 values below to turn auth on.

   1.  Go to https://supabase.com  →  "New project" (free tier is fine).
   2.  Once it's created:  Project Settings (gear)  →  "API".
   3.  Copy "Project URL"  and  the "anon" / "public" key.
   4.  Paste them below, replacing the YOUR_… placeholders, and save.
   5.  In Supabase → Authentication → URL Configuration, add your site URL
       (e.g. http://localhost:8791  and your deployed URL) to "Redirect URLs".

   NOTE: the "anon" key is PUBLIC by design — it's safe in client-side code
   (Supabase protects data with Row-Level Security). NEVER paste the
   "service_role" key here — that one is secret.
   ───────────────────────────────────────────────────────────────────────── */

window.SUPABASE_URL      = "https://szzrnyxjwxfdalwoxtej.supabase.co";       // e.g. https://abcdwxyz.supabase.co
window.SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InN6enJueXhqd3hmZGFsd294dGVqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA3OTMxMzgsImV4cCI6MjA5NjM2OTEzOH0.biJabbevvVRdBbJ53R4_EBDqAdS31P60rb2PPQB0S2U";   // long "eyJ…" anon/public key
