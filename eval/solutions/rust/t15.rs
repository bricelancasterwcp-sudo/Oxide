fn main() {
    let inputs = ["12", "x", "30"];
    let mut sum = 0;
    let mut failed = 0;
    for s in inputs {
        match s.parse::<i64>() {
            Ok(n) => sum += n,
            Err(_) => failed += 1,
        }
    }
    println!("{}", sum);
    println!("{}", failed);
}
