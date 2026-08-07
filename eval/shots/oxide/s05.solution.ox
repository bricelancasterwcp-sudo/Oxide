struct Book { title: Str, pages: Int }

fn main() {
    let b = Book { title: "dune", pages: 412 }
    print_str(b.title)
    print(b.pages)
}
