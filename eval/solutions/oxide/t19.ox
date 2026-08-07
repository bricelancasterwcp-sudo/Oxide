struct Rect { w: Int, h: Int }

fn main() {
    let r = Rect { w: 7, h: 4 }
    print(r.w * r.h)
    print(2 * (r.w + r.h))
}
