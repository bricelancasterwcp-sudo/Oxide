struct Item {
    name: String,
    price: i64,
    qty: i64,
}

fn main() {
    let items = vec![
        Item { name: String::from("pen"), price: 3, qty: 4 },
        Item { name: String::from("pad"), price: 9, qty: 2 },
        Item { name: String::from("ink"), price: 5, qty: 3 },
    ];
    let mut total = 0;
    let mut best = 0;
    let mut best_name = String::new();
    for it in &items {
        let cost = it.price * it.qty;
        total += cost;
        if cost > best {
            best = cost;
            best_name = it.name.clone();
        }
    }
    println!("{}", total);
    println!("{}", best_name);
}
