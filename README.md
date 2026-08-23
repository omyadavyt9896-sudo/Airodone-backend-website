# airodrone – Flask Website

A clean, modern, and fully responsive website for an innovation / ATL lab by airodrone, built with Flask and SQLite.  
Pages include Home, About, Services (with 8 detailed services), Contact (with database-backed form), and an Admin panel for viewing enquiries.

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
    services.html
    service-detail.html
    contact.html
    admin.html
  /static
    /css
      style.css
    /js
      main.js
    /images
      /home
      /services
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

- `/` – Home page (hero, overview, and featured services)
- `/about` – About the lab and its approach
- `/services` – Main services grid with 8 service cards
- `/services/<slug>` – Detailed service page for each service
- `/contact` – Contact form (stores data in SQLite)
- `/admin` – Simple admin view listing all contact submissions

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

For production:

- Use a WSGI server such as Gunicorn or uWSGI in front of the Flask app.
- Disable debug mode in `app.py`:

```python
if __name__ == "__main__":
    app.run(debug=False)
```

- Configure environment variables for any sensitive settings if you extend the app.


