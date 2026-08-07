fn main() {
    let v: Vec<i64> = (1..9).map(|i| i * 3).collect();
    let s: i64 = v.iter().sum();
    println!("{}", s);
}
