fn main() {
    let s = "stack"
    let rev = ""
    for c in chars(&s) {
        rev = concat(c, rev)
    }
    drop s
    print_str(&rev)
    drop rev
}
