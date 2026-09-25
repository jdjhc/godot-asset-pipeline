import Foundation
import AVFoundation
import CoreImage
import ImageIO
import UniformTypeIdentifiers
let asset = AVURLAsset(url: URL(fileURLWithPath: CommandLine.arguments[1]))
let folder = CommandLine.arguments[2]
let requested = Int(CommandLine.arguments[3])!
let track = try await asset.loadTracks(withMediaType: .video).first!
let transform = try await track.load(.preferredTransform)
let duration = CMTimeGetSeconds(try await asset.load(.duration))
func reader() throws -> (AVAssetReader, AVAssetReaderTrackOutput) {
 let r = try AVAssetReader(asset:asset)
 let o = AVAssetReaderTrackOutput(track:track,outputSettings:[kCVPixelBufferPixelFormatTypeKey as String:kCVPixelFormatType_32BGRA])
 r.add(o); guard r.startReading() else { throw r.error! }; return (r,o)
}
let (r1,o1) = try reader()
var total = 0
while o1.copyNextSampleBuffer() != nil {total += 1}
guard r1.status == .completed && total > 0 else {fatalError("Video decoding failed")}
let count = min(requested,total)
let indices = Set((0..<count).map {Int((Double($0)*Double(total-1)/Double(max(1,count-1))).rounded())})
let (r2,o2) = try reader()
let ctx = CIContext()
var frames = [[String:Any]](); var index = 0
while let sample = o2.copyNextSampleBuffer() {
 defer {index += 1}
 if !indices.contains(index) {continue}
 let time = CMSampleBufferGetPresentationTimeStamp(sample)
 let ci = CIImage(cvPixelBuffer:CMSampleBufferGetImageBuffer(sample)!).transformed(by:transform)
 let cg = ctx.createCGImage(ci,from:ci.extent)!
 let name = "frame_\(index).png"
 let dst = CGImageDestinationCreateWithURL(URL(fileURLWithPath:folder+"/"+name) as CFURL,UTType.png.identifier as CFString,1,nil)!
 CGImageDestinationAddImage(dst,cg,nil)
 guard CGImageDestinationFinalize(dst) else {fatalError("PNG write failed")}
 frames.append(["index":index,"seconds":CMTimeGetSeconds(time),"file":name,"width":cg.width,"height":cg.height])
}
guard r2.status == .completed else {fatalError("Video decoding failed")}
let data = try JSONSerialization.data(withJSONObject:["total_frames":total,"duration":duration,"frames":frames],options:[.prettyPrinted])
try data.write(to:URL(fileURLWithPath:folder+"/frames.json"))
