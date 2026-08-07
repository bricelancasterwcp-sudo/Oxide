fn main() {
    let v = vec![2, 5, 1, 6];
    let sum: i64 = v.iter().sum();
    let min = v.iter().min().unwrap();
    println!("{}", sum);
    println!("{}", min);
}
