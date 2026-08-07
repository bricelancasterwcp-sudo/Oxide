fn main() {
    let s = "flame"
    print(str_len(&s))
    let rev = ""
    for c in chars(&s) {
        rev = concat(c, rev)
    }
    drop s
    print_str(&rev)
    drop rev
}
