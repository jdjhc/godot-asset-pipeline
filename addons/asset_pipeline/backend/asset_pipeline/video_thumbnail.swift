import Foundation
import AVFoundation
import CoreImage
import ImageIO
import UniformTypeIdentifiers
let asset = AVURLAsset(url: URL(fileURLWithPath: CommandLine.arguments[1]))
let track = try await asset.loadTracks(withMediaType: .video).first!
let transform = try await track.load(.preferredTransform)
let reader = try AVAssetReader(asset: asset)
let output = AVAssetReaderTrackOutput(track: track, outputSettings: [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA])
reader.add(output)
guard reader.startReading(), let sample = output.copyNextSampleBuffer(), let buffer = CMSampleBufferGetImageBuffer(sample) else { fatalError("No video frame") }
let original = CIImage(cvPixelBuffer: buffer).transformed(by: transform)
let scale = min(1.0, 640.0 / max(original.extent.width, original.extent.height))
let image = original.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
let cg = CIContext().createCGImage(image, from: image.extent)!
let dst = CGImageDestinationCreateWithURL(URL(fileURLWithPath: CommandLine.arguments[2]) as CFURL, UTType.png.identifier as CFString, 1, nil)!
CGImageDestinationAddImage(dst, cg, nil)
guard CGImageDestinationFinalize(dst) else { fatalError("Cannot write preview") }
reader.cancelReading()
