fn safe_div(a: i64, b: i64) -> Result<i64, String> {
    if b == 0 {
        Err(String::from("undefined"))
    } else {
        Ok(a / b)
    }
}

fn main() {
    for d in [5, 0, 4] {
        match safe_div(100, d) {
            Ok(q) => println!("{}", q),
            Err(m) => println!("{}", m),
        }
    }
}
