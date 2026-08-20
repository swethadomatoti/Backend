# NxGen Tech Academy — Team Context

Reference document so future sessions don't need to re-read the whole codebase. Covers both `Backend/` (Django) and `Frontend/` (React/Vite). Last built: 2026-08-17. Re-verify any specific claim before acting on it if it looks stale — this is a snapshot, not live truth.

## What this is

An education platform ("NxGen Tech Academy") — course catalog (SAP, AI/ML, Python, Data Analytics, Digital Marketing), lead/demo/CRM pipeline, student enrollment + Razorpay payments, instructor course authoring, blog CMS, and role-based dashboards (admin / instructor / student / blog_admin).

- **Backend**: Django 6.0 + DRF, PostgreSQL (Neon), JWT auth (`simplejwt`), Cloudinary for media, Celery + Redis for async/scheduled tasks, Razorpay for payments.
- **Frontend**: Vite + React 18 + TypeScript SPA, bootstrapped via **Lovable**, shadcn/ui (Radix) + Tailwind, deployed on Vercel. Talks to the backend over `axios` + JWT bearer tokens.
- No git repo detected at `d:\Nxgen\NxGen` root in this checkout (despite a `.git` folder existing — worth checking `git status` before assuming history is tracked normally here).

---

## Backend (`Backend/`)

### Setup
- Entry: `manage.py` → `backend.settings`. Settings package `backend/` has `settings.py`, `urls.py`, `wsgi.py`, `asgi.py`, `celery.py`, `middleware.py`.
- `requirements.txt` is UTF-16 encoded and huge (~215 packages) — includes a full data-science/ML stack (pandas, numpy, scikit-learn, xgboost, nltk, matplotlib), PDF tooling (weasyprint, reportlab, fpdf, pyhanko, xhtml2pdf), playwright, and dev tooling, much of it likely unused by the actual app.
- Secrets via `.env`/`.env.example` at Backend root (`DJANGO_SECRET_KEY`, `DATABASE_URL`, Cloudinary, email, Razorpay creds) loaded with `os.getenv`/`python-dotenv`. No secrets hardcoded in `settings.py` beyond a dev fallback `SECRET_KEY`.
- **Database**: PostgreSQL only — `settings.py` hand-parses `DATABASE_URL` (Neon) via a custom helper and **raises `ImproperlyConfigured` if missing**. No SQLite fallback for quick local dev.
- **Auth**: custom user model `accounts.User` (`role`: student/instructor/admin/blog_admin). API auth is JWT (`rest_framework_simplejwt`), refresh at `/api/token/refresh/`.
- **CORS**: `CORS_ALLOW_ALL_ORIGINS = True` + `CORS_ALLOW_CREDENTIALS = True` together — very permissive, worth tightening before hardening.
- **Media**: Cloudinary (`cloudinary_storage`), plus custom "authenticated" storage classes in `courses/storage.py` for signed URLs to private files (lesson files, submissions).
- **Celery**: broker/result backend hardcoded to `redis://127.0.0.1:6379/0` (not env-driven) — won't work outside local dev as-is. Used for emails and blog auto-publish scheduling.
- **Email**: Gmail SMTP. **Payments**: Razorpay.
- `TIME_ZONE = 'Asia/Kolkata'` but `USE_TZ = False` (naive datetimes throughout).
- Installed-but-unused dependencies: `django-allauth`, `drf-spectacular`, `drf-yasg`, `whitenoise` — none wired into `INSTALLED_APPS`/`MIDDLEWARE`. No API schema/docs endpoint exists despite two OpenAPI generators being present.
- `backend/middleware.py` defines `TrailingSlashMiddleware` but it's **not registered** in `MIDDLEWARE` — dead code (pairs with `APPEND_SLASH=False`).
- `.vscode/settings.json` points `python.defaultInterpreterPath` at a stale machine-specific path (`D:\Django project\NexGen\venv\...`) — update for any new dev machine.

### Apps

| App | Purpose | Notes |
|---|---|---|
| `accounts` | Custom `User`, `StudentProfile`, `PasswordResetToken`, unified role-based login/register, OTP password reset | Many alias URL paths for the same 3 password-reset views (frontend-compat shims). `MustChangePasswordPermission` references a non-existent `User.must_change_password` field — dead/broken. Old per-role login views commented out in favor of unified login. |
| `courses` | Category → Course → Module → Lesson → Assignment → Submission; `Batch` (campaign+course+instructor+students) | Largest app (26 migrations, ~74KB views.py). `CourseContent` explicitly marked legacy/kept for backward compat. Several duplicate URL shapes (ID-based vs lesson-path-based) for the same actions. |
| `enrollments` | `Enrollment` (name/email/phone, not user-linked — no `student` FK, just email), `PaymentDetail`, Razorpay order/verify, invoices | `post_save` signal auto-creates student `User`+`StudentProfile` + emails temp creds on approval. Has its own `IsAdminOnly` permission class with **different logic** than `accounts.permissions.IsAdminOnly` (name collision, different semantics — footgun). |
| `Enroll` | **Near-duplicate of `enrollments`** — own `Enrollment` model, own URL prefix `/api/enroll/` | A signal pushes `Enroll.Enrollment` → `enrollments.Enrollment` when fee_status is Paid/Partial. Both apps are live in `INSTALLED_APPS` and both URL prefixes wired — genuine redundant pipeline, flagged for future consolidation, not simply legacy. |
| `LeadManagement` | The actual CRM `Lead` model (fullname/email/phone/status/campaign) | Elaborate Excel bulk-import (openpyxl/xlrd, fuzzy column mapping, auto-creates campaigns). Status-choices `PATCH` mutates `Lead.STATUS_CHOICES` **at runtime in memory** — fragile, not persisted, not thread-safe. |
| `leads` | Public intake forms: `ContactUs`, `DemoSchedule` (simple name/email/phone/course/message) | **Naming collision**: this `DemoSchedule` is a simple public form, unrelated to `Demo.DemoSchedule` (see below) despite identical class name. |
| `Demo` | Real demo-scheduling/attendance subsystem: `DemoSchedule` (campaign+instructor+leads M2M, reschedule chains via self-FK, status machine) + `DemoAttendance` | Has `management/commands/cleanup_duplicate_demos.py` — implies recurring duplicate-demo data problems. Well-tested (~21KB tests.py). |
| `instructors` | `Instructor` model (1:1 User optional, PAN/Aadhaar/bank fields — sensitive PII in DB), registration auto-creates User+creds email | Non-superuser instructors blocked from editing their own bank/PAN/Aadhaar via serializer. |
| `learning` | `LessonProgress` tracking | **Two live bugs**: `SaveProgressView` references `Lesson.duration` which doesn't exist on the model; `LessonDetailView` filters `Enrollment` by a `student` FK that doesn't exist (`Enrollment` only has an `email` CharField). Both would raise at runtime if hit. |
| `blog` | `BlogCategory`, `Tag`, `Blog` (Cloudinary media, draft/published/scheduled, soft-delete) | Celery periodic task auto-publishes scheduled blogs. Serializer does UI-alias translation (`publish_status`↔`status`) to match frontend naming. |
| `campaign` | `Campaign` model | `save()` auto-creates a `courses.Batch` the first time a campaign+course pair is created. |
| `Dashboard` | No models — single `DashboardStatsView` aggregating counts from `LeadManagement.Lead`, `Demo.DemoSchedule`, `campaign.Campaign`, `enrollments.Enrollment` (notably not `Enroll.Enrollment`) | Thin aggregation endpoint only. |
| `student_onboarding` | Stub only (`apps.py` copy-pasted from `enrollments`'s config, no models/urls/views) | **Not in `INSTALLED_APPS`** — inert scaffolding, never built out. |

### Known structural issues to keep in mind
1. **`Enroll` vs `enrollments`** — two parallel live enrollment pipelines reconciled one-way via a signal. Any enrollment-related work should check which one a given URL/feature actually touches.
2. **`leads.DemoSchedule` vs `Demo.DemoSchedule`** — same class name, very different shape/purpose (public form vs internal scheduling system).
3. **Two `IsAdminOnly` permission classes** (`accounts.permissions` vs `enrollments.permissions`) with different authorization logic.
4. `scratch/` at Backend root is developer cruft (bulk-import docs + ad-hoc debug/test scripts), not a real app or test suite — treat as historical notes.
5. Root-level ad-hoc scripts (`test_amount.py`, `test_razorpay_flow.py`, `backfill_assignments.py`, etc.) are manual Django-shell-style scripts, not automated tests. Two are UTF-16 encoded and hardcode a real-looking email as a fixture.

---

## Frontend (`Frontend/`)

### Setup
- Vite + React 18 + TS, name in `package.json` still the Lovable default `vite_react_shadcn_ts`. No test runner configured (no Jest/Vitest/RTL, no test script).
- TypeScript/ESLint strictness deliberately relaxed: `strict: false`, `noImplicitAny: false`, `strictNullChecks: false`; `no-explicit-any`/`no-unused-vars` off in ESLint. Service layers use `any` pervasively.
- `.env`: `VITE_API_BASE_URL=https://nxgentechacademy.com/` — **dev defaults straight to the production backend domain**, no local backend URL configured by default. `VITE_USE_DUMMY_DB` is read in code but not declared in `.env`.
- Deployed on **Vercel** (`vercel.json` SPA rewrite). Dev server forced to port 8080.
- Both `bun.lockb` and `package-lock.json` present (two lockfiles/package managers — inconsistent tooling across contributors).
- Design system: "Educational Academy" — Navy Blue `#000080` primary, Green secondary, HSL CSS variables in `src/index.css`. Dark mode class strategy configured but never actually toggled in UI (`next-themes` installed, unused).

### Routing & pages (`src/App.tsx`, eager-loaded, no code-splitting)
- **Public marketing**: Home, About, Mentors, ContactPage, WhyChooseUs, Partners (`/bsnl-skill-development-partner`), PrivacyPolicy, NotFound.
- **Course catalog — two parallel data systems (see issues below)**: `Courses` (legacy, 4 hardcoded courses) vs `AllCourses`/`CategoryListing`/`CourseDetail`/`SAPCategory` (richer, current `categoryCourses.ts`/`detailedCourses.ts`/`sapCoursesContent.ts` dataset).
- **Auth**: unified `Login` (role selector: student/instructor/admin/blog_admin), Register, ForgotPassword, ChangePassword. Legacy `/student-login` etc. all just render `Login`.
- **Blog**: public `Blogs`/`BlogDetail`; admin `BlogAdmin/*` under `/blog-admin/*`.
- **Admin** (`/admin/*`): MainDashboard, **Presales** (1584 lines — the largest page: campaigns/leads/demo scheduling/conversion/recharts analytics), Students, Instructors, AdminCourses, AdminBatches, AdminAssignments, Finance (Transactions/Invoices/Refunds/Reports).
- **Instructor** (`/instructor/*`): Dashboard, Courses, Lessons, ModuleLessons, Topics, Students, Assignments, Profile.
- **Student** (`/student/*`): Dashboard, Courses, Assignments, Progress, Certificates, Profile, CourseViewer, PaymentHistory, Invoices.
- **Payments** (`/payments`, student): Razorpay checkout flow.

### API layer (`src/api/axiosInstance.ts` + `src/services/*.ts`)
- Single shared axios instance; JWT bearer attached via request interceptor; response interceptor does silent one-time token-refresh-and-retry on 401, else clears `localStorage` and redirects to `/`.
- One service file per backend domain (`courseService`, `enrollmentService`, `enrollService`, `leadService`, `campaignService`, `demoService`, `dashboardService`, `instructorService`, `batchService`, `blogService`, `lessonService`, `moduleService`, `invoiceService`, `paymentService`). No react-query usage despite being installed/provided — all fetching is manual `useEffect` + `useState`.
- **`moduleService.ts` has a localStorage-backed "dummy DB" fallback** that silently activates on network errors or `VITE_USE_DUMMY_DB=true` — real risk of instructors editing fake local data unknowingly if the backend is flaky in production.
- `Login.tsx` calls `axiosInstance` directly rather than through a service module — inconsistent with the rest of the codebase.

### Auth flow
- Login stores `access_token`, `refresh_token`, `user_id`, `username`, `role`, `is_first_login` in plain `localStorage` (no cookies/httpOnly). Redirects by role after login; forces `/change-password` if `is_first_login`.
- `ProtectedRoute` gates role-scoped route trees client-side only — no server re-verification per navigation. Logout (`DashboardLayout`) does a blunt `localStorage.clear()`.
- No app-level AuthContext — every component reads `localStorage` directly.

### Known structural issues to keep in mind
1. **Orphaned pages** (built, not routed anywhere): `AICourse`, `AIMLCourse`, `PythonCourse`, `DataAnalyticsCourse`, `SasTraining`, `Notes`, `SAPCourse`, `SAPCourseDetail`, the entire `pages/Dashboard/*` generation (superseded by `pages/Student`/`pages/Instructor`), and `TrainingModelMenu` (+ `data/training-model.ts`) linking to `/training/*` routes that don't exist.
2. **`LeadForm.tsx` is non-functional** — literal `// TODO: Implement form submission logic here`, and unreferenced anywhere else.
3. **Two parallel course-catalog datasets** feeding different routed pages (`/courses` vs `/all-courses`) — shows materially different catalogs to users depending on which page they land on.
4. Redundant deps: both `react-helmet` and `react-helmet-async` installed (only `-async` wired). `next-themes` unused.
5. Stray committed artifact: `lint-output.txt` at repo root referencing an old path, not gitignored.
6. `index.html` JSON-LD schema still has literal placeholders (`"Your Street Address"`, `"+91-XXXXXXXXXX"`).

---

## Cross-cutting integration notes
- Frontend `enrollService`/`enrollmentService` split mirrors the backend's `Enroll`/`enrollments` app split — when working on enrollment features, check which backend app + which frontend service a given flow actually uses before assuming they're unified.
- Frontend role names (`student`/`instructor`/`admin`/`blog_admin`) map directly to backend `accounts.User.role` choices — consistent across the stack.
- Blog serializer's UI-alias translation (`publish_status`↔`status`, `short_description`↔`excerpt`) exists specifically to match `blogService`'s expected frontend field names — if either side's field naming changes, check the other.

---

## Change Log

### 2026-08-20 — Swetha D + Claude (continued)

**Added — Incremental payments with a per-payment history log**
Previously "Update Payment Details" (`PaymentDialog.tsx`) worked as an absolute overwrite: it prefilled the field with the current cumulative `payment_paid` and PUT the typed value back as the new total — meaning the admin had to already know and re-type the running total to add a new payment. Changed to additive: the admin now only enters the amount being paid *right now*.
- **New model**: `enrollments.PaymentTransaction` (`payment_detail` FK, `amount`, `paid_at` auto-timestamp) — one row per payment, migration `0006_paymenttransaction`, applied locally.
- **Changed**: `EnrollmentPaymentDetailView.post()` (`POST /api/enrollments/<id>/payment-details/`) no longer creates-only-if-missing with an absolute amount — it now always adds the given `payment_paid` amount onto whatever `PaymentDetail.payment_paid` already is (`get_or_create` + `+=` + `.save()`, which recalculates `remaining_balance` and `enrollment.fee_status` via existing model logic), and logs a `PaymentTransaction` row each call. The old inline fee_status-recalculation block in this view was removed as redundant — `PaymentDetail.save()` already does this.
- `PUT`/`PATCH` on the same endpoint (`updatePaymentDetails`) were left as absolute-overwrite, unchanged — nothing currently calls them (frontend now always uses the additive POST), kept only in case something else needs a hard correction/reset later.
- `PaymentDetailSerializer` now nests `transactions` (newest first) — surfaced in `StudentDetailPage.tsx`'s Payment Summary card as a small date/amount history list, and in `PaymentDialog.tsx` as read-only "Total Fee" / "Already Paid" context fields alongside the new blank "New Payment Amount" input.
- Verified end-to-end against the local backend: ₹30,000 fee → pay ₹2,000 → due ₹28,000 → pay ₹1,000 more → due ₹27,000, both transactions logged newest-first.
- The new "Amount Paid" field added earlier today to `EnrollmentForm.tsx` (Add Student flow) is unaffected — it's always the first payment on a brand-new `PaymentDetail` (0 + amount), so additive vs. absolute makes no difference there.

**Fixed — student self-service Razorpay payments were invisible in the new payment log**
`VerifyPaymentView.post()` (`POST /api/enrollments/verify-payment/`, hit by the student "My Payments"/Pay Now flow in `Payments.tsx`) already incremented `PaymentDetail.payment_paid` correctly, but never created a `PaymentTransaction` row — so a payment made by a student showed up correctly in their own running total, but was silently missing from the admin's Payment History log added above (which only saw payments recorded through the admin-side endpoint). Fixed by adding the same `PaymentTransaction.objects.create(...)` call there, logging the same `increment` amount that gets added to `payment_paid`. Verified by simulating a signed Razorpay verify request (test student/enrollment created and deleted via Django shell, since the local test keys can't complete a real Razorpay round trip per the Outstanding item below — confirmed the code takes the existing `except: amount_paid = data.get("amount")` fallback path, which is what actually runs in practice today anyway).

### 2026-08-17 — Swetha D + Claude

**Fixed — `Batch.name` serialization crashes (500 errors)**
`courses.Batch.name` was changed from a `CharField` to a `ForeignKey('campaign.Campaign')` in migrations 0025/0026, but the field name was kept as `name`, and several views still read `batch.name` expecting a string. DRF can't JSON-serialize a raw model instance, so any batch with a linked campaign crashed:
- `instructors/views.py` — `InstructorCoursesView` (`GET /api/instructors/my-courses/`) → fixed to `batch.name.name if batch.name else None`.
- `courses/views.py` — `InstructorBatchListView` (`GET /api/courses/my-batches/`) and `ManageLiveClassView` (`GET /api/courses/batches/<pk>/live-class/`) → same fix applied to both.
- The three email-notification f-strings in `courses/views.py` (~lines 672/727/812) using `assignment.batch.name` were left as-is — safe, since `Campaign.__str__` returns `self.name`.
- **Only bites when a batch actually has students/a batch assigned with a linked campaign** — empty batch lists never hit the broken line, which is why this went unnoticed. Worth grepping for `batch.name` again if new endpoints are added that touch `Batch`.

**Fixed — missing permission checks on payment endpoints**
`enrollments/views.py` — `CreateOrderView` (`POST /api/enrollments/create-order/`) and `VerifyPaymentView` (`POST /api/enrollments/verify-payment/`) had no `permission_classes` set at all, and the project has no `DEFAULT_PERMISSION_CLASSES` configured globally either — so both were fully open to **unauthenticated** requests (DRF's implicit default is `AllowAny`). Added `permission_classes = [IsStudent]` (from `accounts.permissions`) to both, since these are only ever called from the student-facing `Payments.tsx`. Verified: unauthenticated → 401, non-student role → 403, student → reaches the view logic correctly.

### 2026-08-20 — Swetha D + Claude

**Fixed — Admin Transactions ledger showing ₹0 for every row**
`GET /api/enrollments/` (`EnrollmentSerializer`) only exposed payment numbers nested under a `payment_detail` object (`PaymentDetail` is a separate model/table, one-to-many via `related_name="payment_details"`). Several admin frontend pages assume flat top-level fields instead — `Finance/Transactions.tsx`, `Finance/Reports.tsx`, `Finance/Refunds.tsx`, `StudentDetailPage.tsx` all read `tx.fee_amount`/`tx.payment_paid`/`tx.remaining_balance` directly off the enrollment object, which were always `undefined` → rendered as ₹0. Non-money fields (`name`, `email`, `fee_status`) rendered fine since those are real top-level `Enrollment` model fields, which is why only the currency columns were blank.
- Confirmed the nested-vs-flat split is intentional/established elsewhere: student-facing pages (`StudentPaymentHistory.tsx`, `StudentInvoices.tsx`, `Payments.tsx`) correctly consume the nested `payment_detail` shape from `StudentEnrolledCourseSerializer`. Only the admin-side `EnrollmentSerializer` was missing flat fields the admin frontend already expected (`EnrollmentData` TS interface already declared them, unused until now).
- **Fix**: added `fee_amount`/`payment_paid`/`remaining_balance` as `SerializerMethodField`s on `EnrollmentSerializer` (`Backend/enrollments/serializers.py`) that mirror the nested `payment_detail` values (falling back to `course.price` when no `PaymentDetail` row exists yet). No frontend changes needed — fixes Transactions, Reports, Refunds, and StudentDetailPage in one place. `payment_detail`/`payment_details` fields left as-is.
- Note: `payment_details` (plural field, `source='payment_detail'`) on `EnrollmentSerializer` has always silently resolved to nothing in the output — wrong source string (model's reverse accessor is `payment_details`, not singular), but since it's `read_only=True` (→ `required=False`), DRF's `get_attribute` raises `SkipField` instead of erroring. Dead field, harmless, not fixed since nothing reads it.
- Each of the three new method fields does its own `obj.payment_details.first()` query (plus the existing `get_payment_detail` doing the same) — real N+1 on large enrollment lists, not addressed here since list sizes are currently small.

**Outstanding — Razorpay test credentials rejected by Razorpay's API**
`POST /api/enrollments/create-order/` returns `400 {"error": "Razorpay Bad Request: Authentication failed"}` for every attempt, in both local dev and production. Root-caused all the way through: `RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET` in `Backend/.env` are present, correctly formatted, and load cleanly into Django settings with no corruption — the failure is Razorpay's own API rejecting the key pair as invalid/inactive. This blocks the entire student "Pay Now" flow at the very first step (order creation never succeeds, so the Razorpay checkout popup never opens).
- **Not a code issue** — confirmed via direct reproduction against the running server and against Django settings values.
- **Needs**: dashboard access to `dashboard.razorpay.com` → Settings → API Keys, to check whether this test key was regenerated/revoked, and issue a fresh key pair if so.
- **As of end of session, still unresolved** — `.env` has not been updated. Check this section first before re-diagnosing the same 400 from scratch.
