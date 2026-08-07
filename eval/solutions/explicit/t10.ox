fn main() {
    let v = push(push(push(push(push(push(vec(), 4), 1), 7), 3), 9), 2)
    let kept = vec()
    for x in &v {
        if x > 3 {
            kept = push(kept, x)
        }
    }
    drop v
    for y in &kept {
        print(y)
    }
    print(len(&kept))
    drop kept
}
