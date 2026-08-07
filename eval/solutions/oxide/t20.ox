struct Item { name: Str, price: Int, qty: Int }

fn main() {
    let items = push(push(push(vec(), Item { name: "pen", price: 3, qty: 4 }), Item { name: "pad", price: 9, qty: 2 }), Item { name: "ink", price: 5, qty: 3 })
    let total = 0
    let best = 0
    let best_name = ""
    for it in items {
        let cost = it.price * it.qty
        total = total + cost
        if cost > best {
            best = cost
            best_name = it.name
        }
    }
    print(total)
    print_str(best_name)
}
