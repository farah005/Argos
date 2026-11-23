"""Setup commands.

    python -m app.cli create-admin admin
    python -m app.cli create-classroom "Salle 12"          # prints the device key
    python -m app.cli create-teacher prof1 --classroom "Salle 12" --name "Mme Ben Salah"
    python -m app.cli list-users
    python -m app.cli assign-classroom prenom.nom@enit.utm.tn --classroom "Salle 12"
"""
import argparse
import getpass
import secrets

from .db import Classroom, SessionLocal, User, init_db
from .security import hash_key, hash_password


def _password(given: str | None) -> str:
    if given:
        return given
    p = getpass.getpass("Mot de passe : ")
    if len(p) < 8:
        raise SystemExit("8 caractères minimum")
    return p


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("create-admin")
    a.add_argument("username")
    a.add_argument("--password")
    c = sub.add_parser("create-classroom")
    c.add_argument("name")
    t = sub.add_parser("create-teacher")
    t.add_argument("username")
    t.add_argument("--classroom", action="append", default=[])
    t.add_argument("--name", default="")
    t.add_argument("--password")
    s = sub.add_parser("assign-classroom", help="attribue des classes à un enseignant (ex. inscrit par e-mail)")
    s.add_argument("username")
    s.add_argument("--classroom", action="append", required=True)
    sub.add_parser("list-users")
    args = ap.parse_args()

    init_db()
    db = SessionLocal()
    try:
        if args.cmd == "create-admin":
            db.add(User(username=args.username, role="admin", password_hash=hash_password(_password(args.password))))
            db.commit()
            print("Administrateur créé.")
        elif args.cmd == "create-classroom":
            key = secrets.token_urlsafe(24)
            db.add(Classroom(name=args.name, device_key_hash=hash_key(key)))
            db.commit()
            print(f"Classe « {args.name} » créée.\nClé du boîtier (à mettre dans config/edge.yaml) :\n{key}")
        elif args.cmd == "create-teacher":
            rooms = db.query(Classroom).filter(Classroom.name.in_(args.classroom)).all()
            missing = set(args.classroom) - {r.name for r in rooms}
            if missing:
                raise SystemExit(f"Classes inconnues : {missing}")
            u = User(username=args.username, full_name=args.name, role="teacher",
                     password_hash=hash_password(_password(args.password)))
            u.classrooms = rooms
            db.add(u)
            db.commit()
            print(f"Enseignant {args.username} créé ({', '.join(r.name for r in rooms) or 'aucune classe'}).")
        elif args.cmd == "assign-classroom":
            u = db.query(User).filter(User.username.ilike(args.username)).first()
            if not u:
                raise SystemExit(f"Utilisateur inconnu : {args.username}")
            rooms = db.query(Classroom).filter(Classroom.name.in_(args.classroom)).all()
            missing = set(args.classroom) - {r.name for r in rooms}
            if missing:
                raise SystemExit(f"Classes inconnues : {missing}")
            u.classrooms = sorted({*u.classrooms, *rooms}, key=lambda r: r.id)
            db.commit()
            print(f"{u.username} : {', '.join(r.name for r in u.classrooms)}")
        elif args.cmd == "list-users":
            for u in db.query(User).order_by(User.id):
                rooms = ", ".join(r.name for r in u.classrooms) or "aucune classe"
                print(f"{u.id:3d}  {u.role:7s}  {u.username:40s}  {u.full_name}  [{rooms}]")
    finally:
        db.close()


if __name__ == "__main__":
    main()
