# Platen PDF backend

Supabase project **Platen PDF** (`lxwsbkofshgtznplwkvs`), separate from every
other project in the account on purpose: a consumer app's auth table has no
business sharing a blast radius with anything else.

## What is here

| File | Purpose |
| --- | --- |
| `migrations/0001_accounts_and_signatures.sql` | profiles, signatures, triggers, RLS, storage bucket |
| `test_rls.py` | proves one account cannot reach another's data |

## The model

The account is **optional**. Everything in the desktop application works
signed out, with signatures held on the local machine. Signing in exists for
one reason: the same signatures appear on the phone. Documents are never
uploaded - only signature images.

Donation reminders are a **separate** opt-in. Creating an account is not
consent to be emailed; `profiles.reminders_opted_in` has to be set
deliberately, and CAN-SPAM and GDPR both care about the difference.

## Row-level security is the whole security model

There is no application server in front of the database. The desktop app and
the phone both talk to PostgREST and storage directly with the user's own JWT,
so a wrong policy is a data leak rather than a bug. Both tables and the storage
bucket deny by default and grant only to the owning user, and signature files
are keyed on the first path segment: `signatures/<user id>/<signature id>.png`.

Re-run the test after **any** change to a policy:

```powershell
$env:SUPABASE_URL = "https://lxwsbkofshgtznplwkvs.supabase.co"
$env:SUPABASE_ANON_KEY = "<publishable key>"
$env:SUPABASE_SERVICE_KEY = "<secret key>"
.venv\Scripts\python supabase\test_rls.py
```

It creates two throwaway accounts, has one try to read, rename, delete and
plant rows belonging to the other, tries the storage object and folder listing,
repeats the lot anonymously with only the public key, confirms the owner still
has everything, and deletes both accounts. Nineteen checks; all must pass.
