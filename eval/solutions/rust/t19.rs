struct Rect {
    w: i64,
    h: i64,
}

fn main() {
    let r = Rect { w: 7, h: 4 };
    println!("{}", r.w * r.h);
    println!("{}", 2 * (r.w + r.h));
}
