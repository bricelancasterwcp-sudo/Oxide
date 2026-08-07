enum Shape {
    Rect(i64, i64),
    Tri(i64, i64, i64),
}

fn perimeter(s: &Shape) -> i64 {
    match s {
        Shape::Rect(w, h) => 2 * (w + h),
        Shape::Tri(a, b, c) => a + b + c,
    }
}

fn main() {
    let shapes = vec![Shape::Rect(3, 4), Shape::Tri(3, 4, 5), Shape::Rect(2, 5)];
    for s in &shapes {
        println!("{}", perimeter(s));
    }
}
