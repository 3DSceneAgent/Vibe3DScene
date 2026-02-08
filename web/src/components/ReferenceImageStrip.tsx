import type { ReferenceImage } from '../api/types'

type ReferenceImageStripProps = {
  images: ReferenceImage[]
}

export function ReferenceImageStrip({ images }: ReferenceImageStripProps) {
  return (
    <div className="reference-strip">
      {images.map((image) => (
        <div className="reference-card" key={image.id}>
          {image.previewUrl ? (
            <img src={image.previewUrl} alt={image.filename} />
          ) : (
            <div className="reference-placeholder">{image.filename}</div>
          )}
        </div>
      ))}
    </div>
  )
}
