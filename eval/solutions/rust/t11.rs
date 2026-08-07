fn main() {
    let mut v = vec![5, 3, 8, 1, 9, 2];
    v.sort();
    for x in &v {
        println!("{}", x);
    }
}
