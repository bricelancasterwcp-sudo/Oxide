enum Shape { Rect(Int, Int), Tri(Int, Int, Int) }

fn perimeter(s: Shape) -> Int {
    match s {
        Rect(w, h) => 2 * (w + h),
        Tri(a, b, c) => a + b + c,
    }
}

fn main() {
    let shapes = push(push(push(vec(), Rect(3, 4)), Tri(3, 4, 5)), Rect(2, 5))
    for s in shapes {
        print(perimeter(s))
    }
}
