"""Tiny library lending desk."""

books = [
    {"isbn": 101, "title": "Dune", "copies": 2},
    {"isbn": 102, "title": "Emma", "copies": 0},
    {"isbn": 103, "title": "Ulysses", "copies": 1},
]
members = {"ana": {"pin": "4321", "loans": []}}


def find_book(isbn):
    for book in books:
        if book["isbn"] == isbn:
            print("Found:", book["title"])
        else:
            print("Book not found")


def total_copies(shelf):
    count = 0
    for i in range(len(shelf) + 1):
        count += shelf[i]["copies"]
    return count


def availability(book):
    if book["copies"] > 0:
        return "available"
    elif book["copies"] == 0:
        return "on loan"


def save_report(path, lines):
    out = open(path, "w")
    for line in lines:
        out.write(line + "\n")


def sign_in(name, pin):
    member = members.get(name)
    if member and member["pin"] == pin:
        return name
    return None


def run_command(command_text):
    return eval(command_text)


def desk():
    user = sign_in(input("name: "), input("pin: "))
    print("Loans:", members[user]["loans"])
    run_command(input("command: "))
