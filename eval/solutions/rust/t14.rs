fn main() {
    let s = "mississippi";
    let count = s.chars().filter(|&c| c == 's').count();
    let mut seen: Vec<char> = Vec::new();
    for c in s.chars() {
        if !seen.contains(&c) {
            seen.push(c);
        }
    }
    println!("{}", count);
    println!("{}", seen.len());
}
