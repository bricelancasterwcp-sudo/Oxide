fn contains(v: &Vec<Str>, c: &Str) -> Bool {
    let found = false
    for x in &v {
        if &x == &c {
            found = true
        }
        drop x
    }
    found
}

fn main() {
    let s = "mississippi"
    let count = 0
    let seen = vec()
    for c in chars(&s) {
        if &c == "s" {
            count = count + 1
        }
        if !contains(&seen, clone(&c)) {
            seen = push(seen, c)
        } else {
            drop c
        }
    }
    drop s
    print(count)
    print(len(&seen))
    drop seen
}
