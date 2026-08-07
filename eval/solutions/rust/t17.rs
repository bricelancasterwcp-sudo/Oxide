fn main() {
    let v = vec![10, 20, 30];
    for i in [1usize, 5usize] {
        match v.get(i) {
            Some(x) => println!("{}", x),
            None => println!("-1"),
        }
    }
}
