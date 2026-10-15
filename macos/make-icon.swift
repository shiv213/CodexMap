import AppKit
import Foundation

let directory = URL(fileURLWithPath: CommandLine.arguments[1])
try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
for size in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = size * scale
        let image = NSImage(size: NSSize(width: pixels, height: pixels))
        image.lockFocus()
        let rect = NSRect(x: 0, y: 0, width: pixels, height: pixels)
        NSColor(calibratedWhite: 0.97, alpha: 1).setFill()
        NSBezierPath(roundedRect: rect.insetBy(dx: Double(pixels) * 0.075, dy: Double(pixels) * 0.075), xRadius: Double(pixels) * 0.2, yRadius: Double(pixels) * 0.2).fill()
        NSColor(calibratedWhite: 0.15, alpha: 1).setStroke()
        let grid = NSBezierPath()
        grid.lineWidth = max(1, Double(pixels) * 0.012)
        grid.move(to: NSPoint(x: Double(pixels) * 0.5, y: Double(pixels) * 0.23)); grid.line(to: NSPoint(x: Double(pixels) * 0.5, y: Double(pixels) * 0.77))
        grid.move(to: NSPoint(x: Double(pixels) * 0.23, y: Double(pixels) * 0.5)); grid.line(to: NSPoint(x: Double(pixels) * 0.77, y: Double(pixels) * 0.5)); grid.stroke()
        NSColor(calibratedWhite: 0.13, alpha: 1).setFill()
        for (x, y, radius) in [(0.34, 0.66, 0.055), (0.67, 0.34, 0.045), (0.66, 0.68, 0.04)] {
            NSBezierPath(ovalIn: NSRect(x: Double(pixels) * (x - radius), y: Double(pixels) * (y - radius), width: Double(pixels) * radius * 2, height: Double(pixels) * radius * 2)).fill()
        }
        image.unlockFocus()
        let bitmap = NSBitmapImageRep(data: image.tiffRepresentation!)!
        let filename = "icon_\(size)x\(size)\(scale == 2 ? "@2x" : "").png"
        try bitmap.representation(using: .png, properties: [:])!.write(to: directory.appendingPathComponent(filename))
    }
}
