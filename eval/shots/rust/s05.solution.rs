struct Book {
    title: String,
    pages: i64,
}

fn main() {
    let b = Book { title: String::from("dune"), pages: 412 };
    println!("{}", b.title);
    println!("{}", b.pages);
}
