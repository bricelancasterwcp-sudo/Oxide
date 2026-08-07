fn safe_div(a: Int, b: Int) -> Result<Int, Str> {
    if b == 0 {
        Err("undefined")
    } else {
        Ok(a / b)
    }
}

fn show(r: Result<Int, Str>) {
    match r {
        Ok(q) => print(q),
        Err(m) => print_str(m),
    }
}

fn main() {
    show(safe_div(100, 5))
    show(safe_div(100, 0))
    show(safe_div(100, 4))
}
