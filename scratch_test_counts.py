import os
os.environ['TEMP'] = r'F:\temp'
os.environ['TMP'] = r'F:\temp'

from app import app, get_active_learning_categories, get_learning_categories_with_counts

cats_active = get_active_learning_categories()
print("ACTIVE (Public Learn / Home):")
for c in cats_active:
    print(f" - {c['name']}: {c['course_count']} Courses ({c['path_count']} Paths)")

cats_admin = get_learning_categories_with_counts(active_only=False)
print("\nADMIN (Admin Categories):")
for c in cats_admin:
    print(f" - {c['name']}: {c['course_count']} Courses ({c['path_count']} Paths)")
