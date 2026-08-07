fn reverse(s: Str) -> Str {
    let rev = ""
    for c in chars(s) {
        rev = concat(c, rev)
    }
    rev
}

fn is_pal(s: Str) -> Bool {
    reverse(s) == s
}

fn main() {
    print(is_pal("level"))
    print(is_pal("stereo"))
    print(is_pal("rotor"))
}
