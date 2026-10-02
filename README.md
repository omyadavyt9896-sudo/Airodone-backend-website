# airodrone – Flask Website

A clean, modern, and fully responsive website for an innovation / ATL lab by airodrone, built with Flask and PostgreSQL (with SQLite fallback).  
Pages include Home, About, Learn (courses and educational learning paths), Products, Contact (with database-backed form), and an Admin panel.

---

## Project Structure

```text
project-root/
  app.py
  requirements.txt
  README.md
  /templates
    base.html
    home.html
    about.html
    courses.html
    course_detail.html
    products.html
    contact.html
    admin.html
  /static
    /css
      style.css
    /js
      main.js
    /images
      /homer
      /about
      /contact
```

> Note: The SQLite database file `database.db` is created automatically on first run in the project root.

The project already ships with a curated set of sample images under `static/images`.  
If you wish to replace them, keep the same filenames or update the template paths accordingly.

---

## Getting Started

### 1. Create a virtual environment (recommended)

```bash
cd /path/to/project-root
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Run the Flask application

```bash
python app.py
```

The app will start on `http://127.0.0.1:5000/` (or `http://localhost:5000/`).

On the first request, the SQLite database (`database.db`) and `contacts` table will be created automatically.

---

## Pages & Routes
 
- `/` – Home page (hero, overview, and featured learning paths)
- `/about` – About the lab and its approach
- `/learn` – Main educational catalogue and learning pathways (legacy `/courses` automatically redirects here)
- `/learn/<slug>` – Detailed course page for each learning program
- `/products` – Products and kits
- `/contact` – Contact form (stores data in database)
- `/admin` – Admin dashboard and content management

---

## Contact Form & Database

- The contact form captures:
  - Full Name (required)
  - Email (required)
  - Phone
  - Subject
  - Message (required)
- Data is stored in a SQLite database (`database.db`) in a table called `contacts`.
- The `/admin` route displays submissions in a table with:
  - ID, Name, Email, Phone, Subject, Message, Date/Time (UTC)

> **Security note:** The `/admin` page is intentionally open and unauthenticated for simplicity.  
> In production, protect this route with authentication or IP restrictions.

---

## Customization

- Update content in the templates under `/templates` to match your branding and copy.
- Adjust colors, spacing, and typography in `static/css/style.css`.
- Replace the placeholder images in `static/images/**` with your own.

---

## Deployment

For production (e.g. Render / Hostinger / VPS):

- Use Gunicorn with `--timeout 600` to support large video lesson uploads without request termination:

```bash
gunicorn --timeout 600 wsgi:application
```

- Disable debug mode in `app.py`:

```python
if __name__ == "__main__":
    app.run(debug=False)
```

- Configure environment variables (`DATABASE_URL`, `SECRET_KEY`, `ADMIN_DEFAULT_PASSWORD`) in your `.env` or cloud dashboard.


