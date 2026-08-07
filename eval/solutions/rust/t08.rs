fn main() {
    let v = vec![3, 8, -2, 12, 7];
    let count = v.iter().filter(|&&x| x > 0).count();
    let max = v.iter().max().unwrap();
    println!("{}", count);
    println!("{}", max);
}
