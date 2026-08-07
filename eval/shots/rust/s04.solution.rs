fn main() {
    let v = vec![7, 8];
    for i in [0usize, 9usize] {
        match v.get(i) {
            Some(x) => println!("{}", x),
            None => println!("0"),
        }
    }
}
