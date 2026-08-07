fn main() {
    let s = "stack"
    let rev = ""
    for c in chars(s) {
        rev = concat(c, rev)
    }
    print_str(rev)
}
